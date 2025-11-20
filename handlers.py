import logging
import json
from pathlib import Path

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram.error import TimedOut, NetworkError

from config import STORAGE_DIR
from metrics import update_metrics_for_coins
from coin_state import recalc_state_for_coins, get_last_price_from_state
from dca_config import get_symbol_config, upsert_symbol_config, validate_budget_vs_min_notional
from dca_min_notional import get_symbol_min_notional
from dca_models import DCAConfigPerSymbol, apply_anchor_offset
from dca_storage import load_grid_state

from dca_grid import build_and_save_dca_grid
from card_text import build_symbol_card_text
log = logging.getLogger(__name__)

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ РАБОТЫ С COINS ----------

COINS_FILE = Path(STORAGE_DIR) / "coins.json"


def parse_coins_string(raw: str) -> list[str]:
    """Парсит строку вида 'btcusdc, ethusdc' в список ['BTCUSDC', 'ETHUSDC']."""
    parts = [p.strip().upper() for p in raw.split(",")]
    coins: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not p:
            continue
        if p in seen:
            continue
        seen.add(p)
        coins.append(p)
    return coins


def _normalize_coins_list(items: list[str]) -> list[str]:
    """Нормализация списка монет: верхний регистр, обрезка пробелов, без дублей."""
    coins: list[str] = []
    seen: set[str] = set()
    for x in items:
        s = str(x).strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        coins.append(s)
    return coins


def _load_coins_raw() -> dict:
    """Внутренний хелпер: загрузить структуру {coins: [...], active_symbol: ...}.

    Поддерживает старый формат файла (простой список монет).
    """
    if not COINS_FILE.exists():
        return {"coins": [], "active_symbol": None}

    try:
        data = json.loads(COINS_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.exception("Не удалось прочитать coins.json: %s", e)
        return {"coins": [], "active_symbol": None}

    # Старый формат: просто список монет
    if isinstance(data, list):
        coins = _normalize_coins_list(data)
        active = coins[0] if coins else None
        return {"coins": coins, "active_symbol": active}

    # Новый формат: словарь с полем coins
    if isinstance(data, dict):
        coins_raw = data.get("coins") or []
        coins = _normalize_coins_list(coins_raw)
        active = data.get("active_symbol")
        if active is not None:
            active = str(active).strip().upper()
            if active not in coins:
                active = coins[0] if coins else None
        else:
            active = coins[0] if coins else None
        return {"coins": coins, "active_symbol": active}

    return {"coins": [], "active_symbol": None}


def load_coins() -> list[str]:
    """Публичный хелпер: вернуть только список монет."""
    raw = _load_coins_raw()
    return raw.get("coins", [])


def get_active_symbol() -> str | None:
    """Получить текущую активную монету из coins.json (или None)."""
    raw = _load_coins_raw()
    active = raw.get("active_symbol")
    coins = raw.get("coins") or []
    if active and active in coins:
        return active
    return coins[0] if coins else None


def set_active_symbol(symbol: str | None) -> None:
    """Установить активную монету и сохранить в coins.json.

    Если символ не в списке монет — будет выбран первый из списка.
    Если список монет пуст, active_symbol сбрасывается.
    """
    raw = _load_coins_raw()
    coins = raw.get("coins") or []

    if not coins:
        active = None
    else:
        if symbol is None:
            active = coins[0]
        else:
            s = str(symbol).strip().upper()
            active = s if s in coins else coins[0]

    COINS_FILE.parent.mkdir(parents=True, exist_ok=True)
    COINS_FILE.write_text(
        json.dumps({"coins": coins, "active_symbol": active}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_coins(coins: list[str]) -> None:
    """Сохранить список монет и аккуратно обновить active_symbol.

    Если старая активная монета остаётся в списке — она сохраняется.
    Иначе активной становится первая монета из нового списка.
    """
    raw = _load_coins_raw()
    old_active = raw.get("active_symbol")

    new_coins = _normalize_coins_list(coins)
    if old_active and old_active in new_coins:
        active = old_active
    else:
        active = new_coins[0] if new_coins else None

    COINS_FILE.parent.mkdir(parents=True, exist_ok=True)
    COINS_FILE.write_text(
        json.dumps({"coins": new_coins, "active_symbol": active}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ TELEGRAM ----------


async def safe_answer_callback(
    query,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    """Ответ на callback_query с защитой от сетевых таймаутов."""
    try:
        await query.answer(text=text, show_alert=show_alert)
    except TimedOut:
        log.warning(
            "Timeout при answer_callback_query для data=%s",
            getattr(query, "data", None),
        )
    except NetworkError as e:
        log.warning("NetworkError при answer_callback_query: %s", e)



async def safe_edit_message_text(
    query,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Редактирование текста и клавиатуры сообщения с защитой от сетевых таймаутов."""
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup)
    except TimedOut:
        log.warning(
            "Timeout при edit_message_text для data=%s",
            getattr(query, "data", None),
        )
    except NetworkError as e:
        log.warning("NetworkError при edit_message_text: %s", e)


async def safe_edit_reply_markup(
    query,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    """Редактирование клавиатуры сообщения с защитой от сетевых таймаутов."""
    try:
        await query.edit_message_reply_markup(reply_markup=reply_markup)
    except TimedOut:
        log.warning(
            "Timeout при edit_message_reply_markup для data=%s",
            getattr(query, "data", None),
        )
    except NetworkError as e:
        log.warning("NetworkError при edit_message_reply_markup: %s", e)



async def safe_edit_reply_markup_by_id(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    """
    Редактирование клавиатуры произвольного сообщения по chat_id/message_id
    с защитой от сетевых таймаутов.
    """
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
        )
    except TimedOut:
        log.warning(
            "Timeout при edit_message_reply_markup_by_id для chat_id=%s message_id=%s",
            chat_id,
            message_id,
        )
    except NetworkError as e:
        log.warning("NetworkError при edit_message_reply_markup_by_id: %s", e)


async def safe_delete_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
) -> None:
    """Безопасное удаление сообщения пользователя."""
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "Не удалось удалить сообщение %s в чате %s: %s",
            message_id,
            chat_id,
            e,
        )


def build_ok_alert_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для alert-сообщений с кнопкой OK."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text="OK", callback_data="alert:ok")]],
    )


# ---------- БАЗОВЫЕ КОМАНДЫ (/start, /help) ----------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Простейший /start для проверки, что бот живой."""
    user = update.effective_user
    log.info(
        "Команда /start от пользователя id=%s username=%s",
        user.id,
        user.username,
    )
    await update.message.reply_text("Привет! Бот-закупщик запущен (локально).")
    # Удаляем команду пользователя
    await safe_delete_message(
        context,
        update.effective_chat.id,
        update.effective_message.id,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /help: показывает Bot_commands.txt и удаляет команду."""
    log.info("Команда /help")
    try:
        text = Path("Bot_commands.txt").read_text(encoding="utf-8")
    except FileNotFoundError:
        text = "Файл Bot_commands.txt пока не создан."

    await update.message.reply_text(text, reply_markup=build_ok_alert_keyboard())
    await safe_delete_message(
        context,
        update.effective_chat.id,
        update.effective_message.id,
    )


# ---------- КОМАНДА /coins ----------


async def coins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /coins: чтение/обновление списка монет в alert, удаляет команду."""
    log.info("Команда /coins")
    message = update.message
    chat_id = update.effective_chat.id
    message_id = update.effective_message.id

    text = (message.text or "").strip()
    parts = text.split(" ", 1)
    args_str = parts[1].strip() if len(parts) > 1 else ""

    if not args_str:
        # Просто показать текущий список монет
        coins = load_coins()
        if coins:
            alert_text = "Текущий список монет:\n" + ", ".join(coins)
        else:
            alert_text = "Список монет пока пуст."

        await message.reply_text(alert_text, reply_markup=build_ok_alert_keyboard())
        await safe_delete_message(context, chat_id, message_id)
        return

    coins = parse_coins_string(args_str)
    if not coins:
        alert_text = (
            "Не удалось распознать ни одной монеты.\n"
            "Введите монеты через запятую, например: BTCUSDC, ETHUSDC"
        )
        await message.reply_text(alert_text, reply_markup=build_ok_alert_keyboard())
        await safe_delete_message(context, chat_id, message_id)
        return

    save_coins(coins)
    alert_text = "Список монет обновлён:\n" + ", ".join(coins)
    await message.reply_text(alert_text, reply_markup=build_ok_alert_keyboard())
    await safe_delete_message(context, chat_id, message_id)


# ---------- КОМАНДЫ /metrics И /rollover ----------


async def metrics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /metrics: обновление метрик.

    /metrics            — для всех монет из coins.json.
    /metrics <SYMBOL>   — только для указанного тикера.
    """
    log.info("Команда /metrics")
    message = update.message
    if not message:
        return

    chat_id = update.effective_chat.id
    message_id = update.effective_message.id

    # Пытаемся определить, указан ли тикер в команде
    text = (message.text or "").strip()
    args = context.args or []

    if args:
        # Режим /metrics <SYMBOL>
        symbol = args[0].strip().upper()
        coins = [symbol]
        count = 1
        log.info("metrics_cmd: обновление метрик для одного тикера: %s", symbol)
    else:
        # Глобальный режим /metrics — все монеты из списка
        coins = load_coins()
        count = len(coins)
        log.info("metrics_cmd: обновление метрик для %s монет", count)

    if coins:
        try:
            update_metrics_for_coins(coins)
        except Exception as e:  # noqa: BLE001
            # Короткий лог без traceback, чтобы не засорять консоль
            log.error(
                "Ошибка при обновлении метрик для %s: %s",
                coins,
                e,
            )
    else:
        log.warning(
            "Команда /metrics: список монет пуст или тикер не указан, метрики не собираем",
        )

    # После команды /metrics тоже перерисовываем MAIN MENU (если оно уже показано)
    await redraw_main_menu_from_user_data(context)

    await safe_delete_message(context, chat_id, message_id)

    if args and coins:
        # Для /metrics <SYMBOL> — отдельный текст с тикером
        symbol = coins[0]
        text_resp = f"Метрики обновлены для {symbol}."
    else:
        text_resp = f"Метрики обновлены для {count} монет."

    await context.bot.send_message(
        chat_id=chat_id,
        text=text_resp,
    )
async def rollover_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /rollover: пересчёт state.json по всем монетам и короткий toast."""
    log.info("Команда /rollover")
    message = update.message
    if not message:
        return
    chat_id = update.effective_chat.id
    message_id = update.effective_message.id

    coins = load_coins()
    count = len(coins)
    if coins:
        try:
            recalc_state_for_coins(coins)
        except Exception as e:  # noqa: BLE001
            log.exception(
                "Ошибка при пересчёте state для монет %s: %s",
                coins,
                e,
            )
    else:
        log.warning(
            "Команда /rollover: список монет пуст, state не пересчитываем",
        )

    await safe_delete_message(context, chat_id, message_id)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Данные пересчитаны для {count} монет.",
    )



async def dca_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /dca start <SYMBOL>: построение DCA-сетки для тикера."""
    log.info("Команда /dca")
    message = update.message
    if not message:
        return

    chat_id = update.effective_chat.id
    message_id = update.effective_message.id

    args = context.args or []
    symbol: str | None = None

    # Ожидаем формат: /dca start SYMBOL
    if len(args) >= 2 and args[0].lower() == "start":
        symbol = args[1].strip().upper()
    else:
        await safe_delete_message(context, chat_id, message_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text="Используйте формат: /dca start SYMBOL",
        )
        return

    # Удаляем команду пользователя, чтобы не засорять чат
    await safe_delete_message(context, chat_id, message_id)

    if not symbol:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Не указан тикер для DCA.",
        )
        return

    cfg = get_symbol_config(symbol)
    if not cfg:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"DCA: конфиг для {symbol} не найден. Задайте BUDGET/LEVELS/ANCHOR.",
        )
        return

    if not getattr(cfg, "enabled", False):
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"DCA: конфигурация для {symbol} не активна.",
        )
        return

    try:
        build_and_save_dca_grid(symbol)
    except ValueError as e:
        # Ошибки работы с конфигом/state/сохранением отдаём как текст
        await context.bot.send_message(
            chat_id=chat_id,
            text=str(e),
        )
        return
    except Exception as e:  # noqa: BLE001
        log.exception("Ошибка при построении DCA-сетки для %s: %s", symbol, e)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Ошибка при построении сетки для {symbol}.",
        )
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Сетка для {symbol} построена",
    )


# ---------- ПОСТРОЕНИЕ ЭКРАНОВ (VIEW-ФУНКЦИИ) ----------


def build_main_menu_text() -> str:
    """Текст главного меню: карточка по активному символу."""
    coins = load_coins()
    if not coins:
        return "Создайте список пар"

    active = get_active_symbol()
    if not active or active not in coins:
        active = coins[0]

    return build_symbol_card_text(active)


def build_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура главного меню.

    Первый ряд: DCA / ORDERS / LOG / MENU.
    Второй ряд (если есть монеты): кнопки с тикерами из coins.json.
    """
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="DCA", callback_data="menu:dca"),
            InlineKeyboardButton(text="ORDERS", callback_data="menu:orders"),
            InlineKeyboardButton(text="LOG", callback_data="menu:log"),
            InlineKeyboardButton(text="MENU", callback_data="menu:menu"),
        ],
    ]

    coins = load_coins()
    if coins:
        coin_row = [
            InlineKeyboardButton(text=symbol, callback_data=f"menu:coin:{symbol}")
            for symbol in coins
        ]
        buttons.append(coin_row)

    return InlineKeyboardMarkup(buttons)


def _get_keyboard_for_current_menu(user_data) -> InlineKeyboardMarkup:
    """Вернуть клавиатуру в зависимости от текущего подменю пользователя."""
    current_menu = user_data.get("current_menu") or "main"

    if current_menu == "dca":
        return build_dca_submenu_keyboard()
    if current_menu == "dca_config":
        return build_dca_config_submenu_keyboard(user_data)
    if current_menu == "dca_run":
        return build_dca_run_submenu_keyboard()
    if current_menu == "menu":
        return build_menu_submenu_keyboard()
    if current_menu == "mode":
        return build_mode_submenu_keyboard()
    if current_menu == "pairs":
        return build_pairs_submenu_keyboard()
    if current_menu == "scheduler":
        return build_scheduler_submenu_keyboard()

    # По умолчанию — главное меню
    return build_main_menu_keyboard()


async def redraw_main_menu_from_query(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Перерисовать главное сообщение (карточку) под теми же кнопками.

    Клавиатура выбирается на основе user_data["current_menu"].
    """
    user_data = context.user_data
    text = build_main_menu_text()
    keyboard = _get_keyboard_for_current_menu(user_data)
    await safe_edit_message_text(query, text, keyboard)


async def redraw_main_menu_from_user_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Перерисовать главное сообщение MAIN MENU, опираясь на chat_id/message_id в user_data.

    Используется в текстовых хэндлерах (BUDGET/LEVELS/ANCHOR/COINS), где у нас нет CallbackQuery.
    """
    user_data = context.user_data
    chat_id = user_data.get("main_menu_chat_id")
    message_id = user_data.get("main_menu_message_id")
    if not chat_id or not message_id:
        return

    text = build_main_menu_text()
    keyboard = _get_keyboard_for_current_menu(user_data)
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=keyboard,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось обновить MAIN MENU по user_data: %s", e)


def build_menu_submenu_keyboard() -> InlineKeyboardMarkup:
    """Подменю для кнопки MENU: MODE, PAIRS, SCHEDULER + назад."""
    buttons = [
        [
            InlineKeyboardButton(text="MODE", callback_data="menu:submenu:mode"),
            InlineKeyboardButton(text="PAIRS", callback_data="menu:submenu:pairs"),
            InlineKeyboardButton(
                text="SCHEDULER",
                callback_data="menu:submenu:scheduler",
            ),
        ],
        [InlineKeyboardButton(text="↩️", callback_data="menu:back:main")],
    ]
    return InlineKeyboardMarkup(buttons)


def build_mode_submenu_keyboard() -> InlineKeyboardMarkup:
    """Подменю MODE: SIM, LIVE + назад."""
    buttons = [
        [
            InlineKeyboardButton(text="SIM", callback_data="menu:mode:sim"),
            InlineKeyboardButton(text="LIVE", callback_data="menu:mode:live"),
        ],
        [InlineKeyboardButton(text="↩️", callback_data="menu:back:menu")],
    ]
    return InlineKeyboardMarkup(buttons)


def build_pairs_submenu_keyboard() -> InlineKeyboardMarkup:
    """Подменю PAIRS: COINS, METRICS, ROLLOVER + назад."""
    buttons = [
        [
            InlineKeyboardButton(text="COINS", callback_data="menu:pairs:coins"),
            InlineKeyboardButton(text="METRICS", callback_data="menu:pairs:metrics"),
            InlineKeyboardButton(text="ROLLOVER", callback_data="menu:pairs:rollover"),
        ],
        [InlineKeyboardButton(text="↩️", callback_data="menu:back:menu")],
    ]
    return InlineKeyboardMarkup(buttons)


def build_scheduler_submenu_keyboard() -> InlineKeyboardMarkup:
    """Подменю SCHEDULER: PERIOD, PUBLISH, STEP 1, STEP 2 + назад."""
    buttons = [
        [
            InlineKeyboardButton(
                text="PERIOD",
                callback_data="menu:scheduler:period",
            ),
            InlineKeyboardButton(
                text="PUBLISH",
                callback_data="menu:scheduler:publish",
            ),
            InlineKeyboardButton(
                text="STEP 1",
                callback_data="menu:scheduler:step1",
            ),
            InlineKeyboardButton(
                text="STEP 2",
                callback_data="menu:scheduler:step2",
            ),
        ],
        [InlineKeyboardButton(text="↩️", callback_data="menu:back:menu")],
    ]
    return InlineKeyboardMarkup(buttons)


def build_dca_submenu_keyboard() -> InlineKeyboardMarkup:
    """Подменю DCA: CONFIG, RUN + назад."""
    buttons = [
        [
            InlineKeyboardButton(text="CONFIG", callback_data="menu:dca:config"),
            InlineKeyboardButton(text="RUN", callback_data="menu:dca:run"),
        ],
        [InlineKeyboardButton(text="↩️", callback_data="menu:back:main")],
    ]
    return InlineKeyboardMarkup(buttons)


def build_dca_config_submenu_keyboard(user_data: dict | None = None) -> InlineKeyboardMarkup:
    """Подменю DCA/CONFIG: BUDGET, LEVELS, ANCHOR, ON/OFF + мини-подменю ANCHOR."""
    symbol = get_active_symbol()
    enabled_label = "OFF"
    if symbol:
        cfg = get_symbol_config(symbol)
        if cfg and getattr(cfg, "enabled", False):
            enabled_label = "ON"

    anchor_submenu_open = False
    if isinstance(user_data, dict):
        anchor_submenu_open = bool(user_data.get("anchor_submenu_open"))

    budget_btn = InlineKeyboardButton(
        text="BUDGET",
        callback_data="menu:dca:config:budget",
    )
    levels_btn = InlineKeyboardButton(
        text="LEVELS",
        callback_data="menu:dca:config:levels",
    )
    anchor_btn = InlineKeyboardButton(
        text="ANCHOR",
        callback_data="menu:dca:config:anchor",
    )
    onoff_btn = InlineKeyboardButton(
        text=enabled_label,
        callback_data="menu:dca:config:list",
    )
    back_btn = InlineKeyboardButton(text="↩️", callback_data="menu:back:dca")

    if not anchor_submenu_open:
        buttons = [
            [budget_btn, levels_btn, anchor_btn, onoff_btn],
            [back_btn],
        ]
    else:
        buttons = [
            [budget_btn, levels_btn, anchor_btn, onoff_btn],
            [
                InlineKeyboardButton(
                    text="FIX",
                    callback_data="menu:dca:config:anchor_fix",
                ),
                InlineKeyboardButton(
                    text="MA30",
                    callback_data="menu:dca:config:anchor_ma30",
                ),
                InlineKeyboardButton(
                    text="PRICE",
                    callback_data="menu:dca:config:anchor_price",
                ),
            ],
            [back_btn],
        ]

    return InlineKeyboardMarkup(buttons)


def build_dca_run_submenu_keyboard() -> InlineKeyboardMarkup:
    """Подменю DCA/RUN: START, STOP, ROLLOVER, METRICS + назад."""
    buttons = [
        [
            InlineKeyboardButton(
                text="START",
                callback_data="menu:dca:run:start",
            ),
            InlineKeyboardButton(
                text="STOP",
                callback_data="menu:dca:run:stop",
            ),
            InlineKeyboardButton(
                text="ROLLOVER",
                callback_data="menu:dca:run:rollover",
            ),
            InlineKeyboardButton(
                text="METRICS",
                callback_data="menu:dca:run:metrics",
            ),
        ],
        [InlineKeyboardButton(text="↩️", callback_data="menu:back:dca")],
    ]
    return InlineKeyboardMarkup(buttons)


# ---------- КОМАНДА /menu И СТИКЕР ДЛЯ ВЫЗОВА МЕНЮ ----------


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /menu: отправляет главное меню с кнопками верхнего уровня."""
    log.info("Команда /menu")
    text = build_main_menu_text()
    keyboard = build_main_menu_keyboard()
    sent = await update.message.reply_text(text, reply_markup=keyboard)

    # Запоминаем главное сообщение MAIN MENU в user_data
    user_data = context.user_data
    user_data["main_menu_chat_id"] = sent.chat_id
    user_data["main_menu_message_id"] = sent.message_id
    user_data["current_menu"] = "main"

    await safe_delete_message(
        context,
        update.effective_chat.id,
        update.effective_message.id,
    )


async def sticker_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вызов меню по стикером (привязка к конкретному стикеру)."""
    sticker = update.message.sticker
    if not sticker:
        return

    # file_unique_id из примера, который ты прислал
    if sticker.file_unique_id == "AgADtIEAAo33YEg":
        log.info("Стикер-меню получен, показываю MAIN MENU")
        text = build_main_menu_text()
        keyboard = build_main_menu_keyboard()
        sent = await update.message.reply_text(text, reply_markup=keyboard)

        # Запоминаем главное сообщение MAIN MENU в user_data
        user_data = context.user_data
        user_data["main_menu_chat_id"] = sent.chat_id
        user_data["main_menu_message_id"] = sent.message_id
        user_data["current_menu"] = "main"
    else:
        log.debug("Получен стикер, но не меню: %s", sticker.file_unique_id)


# ---------- CALLBACK-КНОПКИ МЕНЮ И ПОДМЕНЮ ----------


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка нажатий на кнопки меню и подменю."""
    query = update.callback_query
    data = query.data or ""
    log.info("Callback из меню: %s", data)

    # Запоминаем главное сообщение для последующей перерисовки из текстовых хэндлеров
    user_data = context.user_data
    try:
        user_data["main_menu_chat_id"] = query.message.chat_id
        user_data["main_menu_message_id"] = query.message.message_id
    except Exception:  # noqa: BLE001
        pass

    # Выбор активной монеты через динамические кнопки
    if data.startswith("menu:coin:"):
        symbol = data.split(":", 2)[2]
        set_active_symbol(symbol)
        await safe_answer_callback(query)
        await redraw_main_menu_from_query(query, context)
        return

    # Навигация по меню/подменю
    if data == "menu:dca":
        await safe_answer_callback(query)
        user_data["current_menu"] = "dca"
        await safe_edit_reply_markup(
            query,
            reply_markup=build_dca_submenu_keyboard(),
        )
        return

    if data == "menu:dca:config":
        # Перед открытием подменю CONFIG проверяем, что есть активная пара
        # и по ней нет активной кампании. Если кампания активна, доступ к CONFIG блокируем.
        symbol = get_active_symbol()
        if not symbol:
            await safe_answer_callback(
                query,
                text="Нет выбранной пары для DCA.",
                show_alert=True,
            )
            return

        # Проверяем, нет ли активной кампании (campaign_start_ts есть, а campaign_end_ts нет)
        state = load_grid_state(symbol)
        if state and state.campaign_start_ts and not state.campaign_end_ts:
            await safe_answer_callback(
                query,
                text="Для изменения конфига остановите текущую компанию",
                show_alert=True,
            )
            return

        await safe_answer_callback(query)
        user_data["current_menu"] = "dca_config"
        user_data["anchor_submenu_open"] = False
        await safe_edit_reply_markup(
            query,
            reply_markup=build_dca_config_submenu_keyboard(user_data),
        )
        return

    if data == "menu:dca:run":
        await safe_answer_callback(query)
        user_data["current_menu"] = "dca_run"
        await safe_edit_reply_markup(
            query,
            reply_markup=build_dca_run_submenu_keyboard(),
        )
        return

    if data == "menu:back:dca":
        await safe_answer_callback(query)
        user_data["current_menu"] = "dca"
        await safe_edit_reply_markup(
            query,
            reply_markup=build_dca_submenu_keyboard(),
        )
        return

    if data == "menu:menu":
        await safe_answer_callback(query)
        user_data["current_menu"] = "menu"
        await safe_edit_reply_markup(
            query,
            reply_markup=build_menu_submenu_keyboard(),
        )
        return

    if data == "menu:submenu:mode":
        await safe_answer_callback(query)
        user_data["current_menu"] = "mode"
        await safe_edit_reply_markup(
            query,
            reply_markup=build_mode_submenu_keyboard(),
        )
        return

    if data == "menu:submenu:pairs":
        await safe_answer_callback(query)
        user_data["current_menu"] = "pairs"
        await safe_edit_reply_markup(
            query,
            reply_markup=build_pairs_submenu_keyboard(),
        )
        return

    if data == "menu:submenu:scheduler":
        await safe_answer_callback(query)
        user_data["current_menu"] = "scheduler"
        await safe_edit_reply_markup(
            query,
            reply_markup=build_scheduler_submenu_keyboard(),
        )
        return

    if data == "menu:back:main":
        await safe_answer_callback(query)
        user_data["current_menu"] = "main"
        await safe_edit_reply_markup(
            query,
            reply_markup=build_main_menu_keyboard(),
        )
        return

    if data == "menu:back:menu":
        await safe_answer_callback(query)
        user_data["current_menu"] = "menu"
        await safe_edit_reply_markup(
            query,
            reply_markup=build_menu_submenu_keyboard(),
        )
        return

    if data == "menu:pairs:metrics":
        # Сбор метрик по всем монетам через кнопку METRICS
        coins = load_coins()
        count = len(coins)
        if coins:
            try:
                update_metrics_for_coins(coins)
            except Exception as e:  # noqa: BLE001
                # Короткий лог без traceback
                log.error(
                    "Ошибка при обновлении метрик (METRICS) для %s: %s",
                    coins,
                    e,
                )
        else:
            log.warning(
                "Кнопка METRICS: список монет пуст, метрики не собираем",
            )

        await safe_answer_callback(
            query,
            text=f"Метрики обновлены для {count} монет.",
            show_alert=False,
        )
        # После обновления метрик перерисовываем MAIN MENU
        await redraw_main_menu_from_query(query, context)
        return

    if data == "menu:pairs:coins":
        # Ввод монет через кнопку COINS:
        # 1) показываем alert с текущим списком монет
        # 2) отправляем служебное сообщение "Введите список монет..."
        coins = load_coins()
        if coins:
            alert_text = "Текущий список монет:\n" + ", ".join(coins)
        else:
            alert_text = "Список монет пока пуст."
        await safe_answer_callback(query, text=alert_text, show_alert=True)

        chat_id = query.message.chat_id
        text = (
            "Введите список монет через запятую\n"
            "пример: BTCUSDC, ETHUSDC, SOLUSDC"
        )
        waiting = await context.bot.send_message(chat_id=chat_id, text=text)
        context.user_data["await_state"] = "coins_input"
        context.user_data["await_message_id"] = waiting.message_id
        return

    if data == "menu:pairs:rollover":
        # Пересчёт state.json по всем монетам через кнопку ROLLOVER
        coins = load_coins()
        count = len(coins)
        if coins:
            try:
                recalc_state_for_coins(coins)
            except Exception as e:  # noqa: BLE001
                log.exception(
                    "Ошибка при пересчёте state (ROLLOVER) для %s: %s",
                    coins,
                    e,
                )
        else:
            log.warning(
                "Кнопка ROLLOVER: список монет пуст, state не пересчитываем",
            )

        await safe_answer_callback(
            query,
            text=f"Данные пересчитаны для {count} монет.",
            show_alert=False,
        )
        await redraw_main_menu_from_query(query, context)
        return
    if data == "menu:dca:run:start":
        # Построение DCA-сетки только для активного тикера через DCA/RUN → START
        symbol = get_active_symbol()
        if not symbol:
            await safe_answer_callback(
                query,
                text="Нет выбранной пары для START.",
                show_alert=False,
            )
            return

        cfg = get_symbol_config(symbol)
        if not cfg:
            await safe_answer_callback(
                query,
                text=f"DCA: конфиг для {symbol} не найден. Задайте BUDGET/LEVELS/ANCHOR.",
                show_alert=True,
            )
            return

        if not getattr(cfg, "enabled", False):
            await safe_answer_callback(
                query,
                text=f"DCA: конфигурация для {symbol} не активна.",
                show_alert=False,
            )
            return

        try:
            build_and_save_dca_grid(symbol)
        except ValueError as e:
            await safe_answer_callback(
                query,
                text=str(e),
                show_alert=True,
            )
            return
        except Exception as e:  # noqa: BLE001
            log.exception(
                "Ошибка при построении DCA-сетки (START) для %s: %s",
                symbol,
                e,
            )
            await safe_answer_callback(
                query,
                text=f"Ошибка при построении сетки для {symbol}.",
                show_alert=True,
            )
            return

        await safe_answer_callback(
            query,
            text=f"Сетка для {symbol} построена",
            show_alert=False,
        )
        await redraw_main_menu_from_query(query, context)
        return

    if data == "menu:dca:run:rollover":
        # Пересчёт state только для активного тикера через DCA/RUN → ROLLOVER
        symbol = get_active_symbol()
        if not symbol:
            await safe_answer_callback(
                query,
                text="Нет выбранной пары для ROLLOVER.",
                show_alert=False,
            )
            return

        try:
            recalc_state_for_coins([symbol])
        except Exception as e:  # noqa: BLE001
            log.exception(
                "Ошибка при пересчёте state (DCA RUN ROLLOVER) для %s: %s",
                symbol,
                e,
            )

        await safe_answer_callback(
            query,
            text=f"Данные пересчитаны для {symbol}.",
            show_alert=False,
        )
        await redraw_main_menu_from_query(query, context)
        return

    if data == "menu:dca:run:metrics":
        # Обновление метрик только для активного тикера через DCA/RUN → METRICS
        symbol = get_active_symbol()
        if not symbol:
            await safe_answer_callback(
                query,
                text="Нет выбранной пары для METRICS.",
                show_alert=False,
            )
            return

        try:
            update_metrics_for_coins([symbol])
        except Exception as e:  # noqa: BLE001
            # Короткий лог без traceback
            log.error(
                "Ошибка при обновлении метрик (DCA RUN METRICS) для %s: %s",
                symbol,
                e,
            )

        await safe_answer_callback(
            query,
            text=f"Метрики обновлены для {symbol}.",
            show_alert=False,
        )
        await redraw_main_menu_from_query(query, context)
        return
    if data == "menu:dca:config:budget":
        # Ввод бюджета для активного тикера через DCA/CONFIG → BUDGET
        symbol = get_active_symbol()
        if not symbol:
            await safe_answer_callback(
                query,
                text="Нет выбранной пары для BUDGET.",
                show_alert=True,
            )
            return

        # Проверяем, нет ли активной кампании (campaign_start_ts есть, а campaign_end_ts нет)
        state = load_grid_state(symbol)
        if state and state.campaign_start_ts and not state.campaign_end_ts:
            await safe_answer_callback(
                query,
                text="Для изменения конфига остановите текущую компанию",
                show_alert=True,
            )
            return

        user_data["anchor_submenu_open"] = False
        await safe_answer_callback(query)
        chat_id = query.message.chat_id
        text = (
            f"Введите бюджет в USDC для {symbol}.\n"
            "Введите целое число больше нуля, например: 100"
        )
        waiting = await context.bot.send_message(chat_id=chat_id, text=text)
        context.user_data["await_state"] = "dca_budget_input"
        context.user_data["await_message_id"] = waiting.message_id
        context.user_data["budget_symbol"] = symbol
        return

    if data == "menu:dca:config:levels":
        # Ввод количества уровней для активного тикера через DCA/CONFIG → LEVELS
        symbol = get_active_symbol()
        if not symbol:
            await safe_answer_callback(
                query,
                text="Нет выбранной пары для LEVELS.",
                show_alert=True,
            )
            return

        # Проверяем, нет ли активной кампании (campaign_start_ts есть, а campaign_end_ts нет)
        state = load_grid_state(symbol)
        if state and state.campaign_start_ts and not state.campaign_end_ts:
            await safe_answer_callback(
                query,
                text="Для изменения конфига остановите текущую компанию",
                show_alert=True,
            )
            return

        user_data["anchor_submenu_open"] = False
        await safe_answer_callback(query)
        chat_id = query.message.chat_id
        text = (
            f"Введите количество уровней для {symbol}.\n"
            "Введите целое число больше нуля, например: 10"
        )
        waiting = await context.bot.send_message(chat_id=chat_id, text=text)
        context.user_data["await_state"] = "dca_levels_input"
        context.user_data["await_message_id"] = waiting.message_id
        context.user_data["levels_symbol"] = symbol
        return

    if data == "menu:dca:config:anchor":
        # Переключение мини-подменю ANCHOR для активного тикера через DCA/CONFIG → ANCHOR
        symbol = get_active_symbol()
        if not symbol:
            await safe_answer_callback(
                query,
                text="Нет выбранной пары для ANCHOR.",
                show_alert=True,
            )
            return

        # Проверяем, нет ли активной кампании (campaign_start_ts есть, а campaign_end_ts нет)
        state = load_grid_state(symbol)
        if state and state.campaign_start_ts and not state.campaign_end_ts:
            await safe_answer_callback(
                query,
                text="Для изменения конфига остановите текущую компанию",
                show_alert=True,
            )
            return

        await safe_answer_callback(query)
        current = bool(user_data.get("anchor_submenu_open"))
        user_data["anchor_submenu_open"] = not current
        await safe_edit_reply_markup(
            query,
            reply_markup=build_dca_config_submenu_keyboard(user_data),
        )
        return

    if data in (
        "menu:dca:config:anchor_fix",
        "menu:dca:config:anchor_ma30",
        "menu:dca:config:anchor_price",
    ):
        # Обработчики мини-подменю ANCHOR (FIX / MA30 / PRICE) — без изменения конфига.
        symbol = get_active_symbol()
        if not symbol:
            await safe_answer_callback(
                query,
                text="Нет выбранной пары для ANCHOR.",
                show_alert=True,
            )
            return

        # Проверяем, нет ли активной кампании (campaign_start_ts есть, а campaign_end_ts нет)
        state = load_grid_state(symbol)
        if state and state.campaign_start_ts and not state.campaign_end_ts:
            await safe_answer_callback(
                query,
                text="Для изменения конфига остановите текущую компанию",
                show_alert=True,
            )
            return

        if data == "menu:dca:config:anchor_fix":
            # Шаг 5.3 — полноценный сценарий ввода фиксированного anchor (режим FIX).
            await safe_answer_callback(query)
            chat_id = query.message.chat_id
            text = (
                f"Введите фиксированный anchor для {symbol}.\n"
                "Например: 1.2345"
            )
            waiting = await context.bot.send_message(chat_id=chat_id, text=text)
            context.user_data["await_state"] = "dca_anchor_input"
            context.user_data["await_message_id"] = waiting.message_id
            context.user_data["anchor_symbol"] = symbol
            return


        if data == "menu:dca:config:anchor_ma30":
            # Режим MA30 + offset: при нажатии показываем запрос на ввод offset.
            await safe_answer_callback(query)
            chat_id = query.message.chat_id
            text = (
                "Введите offset слежения за MA30\n"
                "Примеры: 100, -10, 2%, -3%"
            )
            waiting = await context.bot.send_message(chat_id=chat_id, text=text)
            context.user_data["await_state"] = "dca_anchor_ma30_input"
            context.user_data["await_message_id"] = waiting.message_id
            context.user_data["anchor_symbol"] = symbol
            return

            upsert_symbol_config(cfg)

            # Короткий toast без alert-окна
            await safe_answer_callback(
                query,
                text="Режим ANCHOR: MA30",
                show_alert=False,
            )

            # После изменения конфига перерисовываем MAIN MENU с учётом текущего подменю
            await redraw_main_menu_from_user_data(context)
            return

            return

        if data == "menu:dca:config:anchor_price":
            # Режим PRICE + offset: при нажатии показываем запрос на ввод offset.
            await safe_answer_callback(query)
            chat_id = query.message.chat_id
            text = (
                "Введите offset слежения за PRICE\n"
                "Примеры: 100, -10, 2%, -3%"
            )
            waiting = await context.bot.send_message(chat_id=chat_id, text=text)
            context.user_data["await_state"] = "dca_anchor_price_input"
            context.user_data["await_message_id"] = waiting.message_id
            context.user_data["anchor_symbol"] = symbol
            return

            upsert_symbol_config(cfg)

            # Короткий toast без alert-окна
            await safe_answer_callback(
                query,
                text="Режим ANCHOR: PRICE",
                show_alert=False,
            )

            # После изменения конфига перерисовываем MAIN MENU с учётом текущего подменю
            await redraw_main_menu_from_user_data(context)
            return

            return

    if data == "menu:dca:config:list":
        # Кнопка ON/OFF в подменю DCA/CONFIG — включение/выключение DCA для активного тикера
        symbol = get_active_symbol()
        if not symbol:
            await safe_answer_callback(
                query,
                text="Нет выбранной пары для DCA.",
                show_alert=True,
            )
            return

        # Проверяем, нет ли активной кампании (campaign_start_ts есть, а campaign_end_ts нет)
        state = load_grid_state(symbol)
        if state and state.campaign_start_ts and not state.campaign_end_ts:
            await safe_answer_callback(
                query,
                text="Для изменения конфига остановите текущую компанию",
                show_alert=True,
            )
            return

        cfg = get_symbol_config(symbol)
        if not cfg:
            cfg = DCAConfigPerSymbol(symbol=symbol)

        user_data["anchor_submenu_open"] = False
        # Сохраняем информацию о сообщении меню, чтобы потом обновить подпись кнопки
        context.user_data["dca_config_menu_chat_id"] = query.message.chat_id
        context.user_data["dca_config_menu_msg_id"] = query.message.message_id

        await safe_answer_callback(query)

        # В зависимости от текущего состояния готовим текст и тип действия
        if cfg.enabled:
            text = "Деактивировать настройки DCA?"
            action = "disable"
        else:
            text = "Активировать настройки DCA?"
            action = "enable"

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅", callback_data="menu:dca:enable:yes"),
                    InlineKeyboardButton("❌", callback_data="menu:dca:enable:no"),
                ]
            ]
        )
        msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=keyboard,
        )

        # Сохраняем состояние ожидания подтверждения
        context.user_data["await_state"] = "dca_enable_confirm"
        context.user_data["enable_symbol"] = symbol
        context.user_data["enable_action"] = action
        context.user_data["enable_message_id"] = msg.message_id
        return

    if data in ("menu:dca:enable:yes", "menu:dca:enable:no"):
        # Обработка подтверждения/отмены включения/выключения DCA
        user_data = context.user_data
        symbol = user_data.get("enable_symbol")
        action = user_data.get("enable_action")
        waiting_message_id = user_data.get("enable_message_id")
        confirm_chat_id = query.message.chat_id

        # Удаляем сообщение с вопросом и кнопками, если оно ещё есть
        if waiting_message_id:
            await safe_delete_message(context, confirm_chat_id, waiting_message_id)

        # Считываем и потом очищаем информацию о меню конфигурации
        menu_chat_id = user_data.get("dca_config_menu_chat_id")
        menu_message_id = user_data.get("dca_config_menu_msg_id")

        # Сбрасываем состояние ожидания
        user_data.pop("await_state", None)
        user_data.pop("enable_symbol", None)
        user_data.pop("enable_action", None)
        user_data.pop("enable_message_id", None)

        # Ветка отмены (❌)
        if data == "menu:dca:enable:no":
            # Просто отменяем действие, ничего не меняем в конфиге
            await safe_answer_callback(
                query,
                text="Действие отменено",
                show_alert=False,
            )
            # Перерисовываем меню, если возможно
            if menu_chat_id and menu_message_id:
                await safe_edit_reply_markup_by_id(
                    context,
                    menu_chat_id,
                    menu_message_id,
                    build_dca_config_submenu_keyboard(user_data),
                )
            # Очищаем сохранённые идентификаторы меню
            user_data.pop("dca_config_menu_chat_id", None)
            user_data.pop("dca_config_menu_msg_id", None)
            return

        # data == "menu:dca:enable:yes" — пользователь подтвердил действие
        if not symbol or not action:
            await safe_answer_callback(
                query,
                text="Не удалось определить пару или действие для DCA.",
                show_alert=True,
            )
            # На всякий случай пробуем обновить меню
            if menu_chat_id and menu_message_id:
                await safe_edit_reply_markup_by_id(
                    context,
                    menu_chat_id,
                    menu_message_id,
                    build_dca_config_submenu_keyboard(user_data),
                )
            user_data.pop("dca_config_menu_chat_id", None)
            user_data.pop("dca_config_menu_msg_id", None)
            return

        cfg = get_symbol_config(symbol)
        if not cfg:
            cfg = DCAConfigPerSymbol(symbol=symbol)

        # Ветка выключения (ON -> OFF)
        if action == "disable":
            cfg.enabled = False
            upsert_symbol_config(cfg)
            await safe_answer_callback(
                query,
                text="DCA не активен",
                show_alert=False,
            )

            # Обновляем меню конфигурации
            if menu_chat_id and menu_message_id:
                await safe_edit_reply_markup_by_id(
                    context,
                    menu_chat_id,
                    menu_message_id,
                    build_dca_config_submenu_keyboard(user_data),
                )

            user_data.pop("dca_config_menu_chat_id", None)
            user_data.pop("dca_config_menu_msg_id", None)
            return

        # Ветка включения (OFF -> ON) с проверкой бюджета
        if action == "enable":
            try:
                min_notional = get_symbol_min_notional(symbol)
            except Exception as e:  # noqa: BLE001
                log.exception(
                    "Не удалось получить minNotional для %s при включении DCA: %s",
                    symbol,
                    e,
                )
                await safe_answer_callback(
                    query,
                    text="Не удалось проверить конфигурацию DCA. Попробуйте позже.",
                    show_alert=True,
                )
                # Обновляем меню (состояние не менялось)
                if menu_chat_id and menu_message_id:
                    await safe_edit_reply_markup_by_id(
                        context,
                        menu_chat_id,
                        menu_message_id,
                        build_dca_config_submenu_keyboard(user_data),
                    )
                user_data.pop("dca_config_menu_chat_id", None)
                user_data.pop("dca_config_menu_msg_id", None)
                return

            ok, _ = validate_budget_vs_min_notional(cfg, min_notional)
            if not ok:
                # Жёсткая проверка — не даём включить, если бюджет недостаточен
                await safe_answer_callback(
                    query,
                    text="Бюджет недостаточен. Измените настройки",
                    show_alert=True,
                )
                if menu_chat_id and menu_message_id:
                    await safe_edit_reply_markup_by_id(
                        context,
                        menu_chat_id,
                        menu_message_id,
                        build_dca_config_submenu_keyboard(user_data),
                    )
                user_data.pop("dca_config_menu_chat_id", None)
                user_data.pop("dca_config_menu_msg_id", None)
                return

            cfg.enabled = True
            upsert_symbol_config(cfg)

            await safe_answer_callback(
                query,
                text="DCA активен",
                show_alert=False,
            )

            if menu_chat_id and menu_message_id:
                await safe_edit_reply_markup_by_id(
                    context,
                    menu_chat_id,
                    menu_message_id,
                    build_dca_config_submenu_keyboard(user_data),
                )

            user_data.pop("dca_config_menu_chat_id", None)
            user_data.pop("dca_config_menu_msg_id", None)
            return






    # Остальные кнопки пока дают только toast-заглушку
    label_map = {
        "menu:orders": "ORDERS раздел пока не реализован.",
        "menu:log": "LOG раздел пока не реализован.",
        "menu:scheduler:period": "Настройка PERIOD пока не реализована.",
        "menu:scheduler:publish": "Настройка PUBLISH пока не реализована.",
        "menu:scheduler:step1": "Настройка STEP 1 пока не реализована.",
        "menu:scheduler:step2": "Настройка STEP 2 пока не реализована.",
        "menu:dca:run:stop": "Остановка DCA (STOP) пока не реализована.",
    }
    msg = label_map.get(data, "Действие пока не реализовано.")

    await safe_answer_callback(query, text=msg, show_alert=False)


# ---------- ALERT: КНОПКА OK ----------


async def alert_ok_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработка нажатия на кнопку OK в alert-сообщениях."""
    query = update.callback_query
    message = query.message
    await safe_answer_callback(query)
    if message:
        try:
            await context.bot.delete_message(
                chat_id=message.chat_id,
                message_id=message.message_id,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось удалить alert-сообщение: %s", e)


# ---------- ОБРАБОТКА ТЕКСТА: ВВОД МОНЕТ И ПРОЧЕЕ ----------


async def handle_dca_budget_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработка текста, когда ждём ввод бюджета после кнопки BUDGET."""
    message = update.message
    if not message:
        return

    chat_id = message.chat_id
    user_msg_id = message.message_id
    user_data = context.user_data

    raw = (message.text or "").strip()
    awaiting_symbol = user_data.get("budget_symbol")
    waiting_message_id = user_data.get("await_message_id")

    # Проверяем, что у нас есть символ, для которого ждём бюджет
    if not awaiting_symbol:
        # Неизвестное состояние — просто чистим сообщение пользователя и выходим
        await safe_delete_message(context, chat_id, user_msg_id)
        if waiting_message_id:
            await safe_delete_message(context, chat_id, waiting_message_id)
        user_data.pop("await_state", None)
        user_data.pop("await_message_id", None)
        user_data.pop("budget_symbol", None)
        return

    # Пытаемся распарсить целое число > 0
    try:
        value = int(raw)
    except ValueError:
        # Некорректный ввод — просто удаляем сообщение пользователя и остаёмся в режиме ожидания
        await safe_delete_message(context, chat_id, user_msg_id)
        return

    if value <= 0:
        # Некорректный ввод — просто удаляем сообщение пользователя и остаёмся в режиме ожидания
        await safe_delete_message(context, chat_id, user_msg_id)
        return

    symbol = str(awaiting_symbol).upper()
    budget_usdc = float(value)

    # Загружаем или создаём конфиг для символа
    cfg = get_symbol_config(symbol)
    if not cfg:
        cfg = DCAConfigPerSymbol(symbol=symbol)
    cfg.budget_usdc = budget_usdc

    # Сохраняем конфиг
    upsert_symbol_config(cfg)

    # Пытаемся выполнить мягкую проверку против minNotional
    soft_warning = False
    try:
        min_notional = get_symbol_min_notional(symbol)
        ok, _ = validate_budget_vs_min_notional(cfg, min_notional)
        if not ok:
            soft_warning = True
    except Exception:
        # Если не удалось получить minNotional или произошла ошибка — считаем, что предупреждение не требуется
        soft_warning = False

    # Удаляем сообщения ожидания и ввода
    await safe_delete_message(context, chat_id, user_msg_id)
    if waiting_message_id:
        await safe_delete_message(context, chat_id, waiting_message_id)

    # Сбрасываем состояние ожидания
    user_data.pop("await_state", None)
    user_data.pop("await_message_id", None)
    user_data.pop("budget_symbol", None)

    # Бюджет успешно сохранён (даже если soft_warning == True) — тихо перерисовываем карточку
    await redraw_main_menu_from_user_data(context)


async def handle_dca_levels_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработка текста, когда ждём ввод количества уровней после кнопки LEVELS."""
    message = update.message
    if not message:
        return

    chat_id = message.chat_id
    user_msg_id = message.message_id
    user_data = context.user_data

    raw = (message.text or "").strip()
    awaiting_symbol = user_data.get("levels_symbol")
    waiting_message_id = user_data.get("await_message_id")

    # Проверяем, что у нас есть символ, для которого ждём количество уровней
    if not awaiting_symbol:
        # Неизвестное состояние — просто чистим сообщение пользователя и выходим
        await safe_delete_message(context, chat_id, user_msg_id)
        if waiting_message_id:
            await safe_delete_message(context, chat_id, waiting_message_id)
        user_data.pop("await_state", None)
        user_data.pop("await_message_id", None)
        user_data.pop("levels_symbol", None)
        return

    # Пытаемся распарсить целое число > 0
    try:
        value = int(raw)
    except ValueError:
        # Некорректный ввод — просто удаляем сообщение пользователя и оставляем режим ожидания
        await safe_delete_message(context, chat_id, user_msg_id)
        return

    if value <= 0:
        # Некорректный ввод — просто удаляем сообщение пользователя и оставляем режим ожидания
        await safe_delete_message(context, chat_id, user_msg_id)
        return

    symbol = str(awaiting_symbol).upper()
    levels_count = int(value)

    # Загружаем или создаём конфиг для символа
    cfg = get_symbol_config(symbol)
    if not cfg:
        cfg = DCAConfigPerSymbol(symbol=symbol)
    cfg.levels_count = levels_count

    # Сохраняем конфиг
    upsert_symbol_config(cfg)

    # Пытаемся выполнить мягкую проверку против minNotional
    soft_warning = False
    try:
        min_notional = get_symbol_min_notional(symbol)
        ok, _ = validate_budget_vs_min_notional(cfg, min_notional)
        if not ok:
            soft_warning = True
    except Exception:
        # Если не удалось получить minNotional или произошла ошибка — считаем, что предупреждение не требуется
        soft_warning = False

    # Удаляем сообщения ожидания и ввода
    await safe_delete_message(context, chat_id, user_msg_id)
    if waiting_message_id:
        await safe_delete_message(context, chat_id, waiting_message_id)

    # Сбрасываем состояние ожидания
    user_data.pop("await_state", None)
    user_data.pop("await_message_id", None)
    user_data.pop("levels_symbol", None)

    # Тихое поведение: без сообщений "сохранено" или "меньше необходимого"
    # Просто перерисовываем MAIN MENU с учётом актуального подменю
    await redraw_main_menu_from_user_data(context)


async def handle_dca_anchor_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработка текста, когда ждём ввод цены ANCHOR после кнопки ANCHOR."""
    message = update.message
    if not message:
        return

    chat_id = message.chat_id
    user_msg_id = message.message_id
    user_data = context.user_data

    raw = (message.text or "").strip()
    awaiting_symbol = user_data.get("anchor_symbol")
    waiting_message_id = user_data.get("await_message_id")

    # Проверяем, что у нас есть символ, для которого ждём anchor
    if not awaiting_symbol:
        # Неизвестное состояние — просто чистим сообщение пользователя и выходим
        await safe_delete_message(context, chat_id, user_msg_id)
        if waiting_message_id:
            await safe_delete_message(context, chat_id, waiting_message_id)
        user_data.pop("await_state", None)
        user_data.pop("await_message_id", None)
        user_data.pop("anchor_symbol", None)
        return

    # Пытаемся распарсить число > 0
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        # Некорректный ввод — просто удаляем сообщение пользователя и оставляем режим ожидания
        await safe_delete_message(context, chat_id, user_msg_id)
        return

    if value <= 0:
        # Некорректный ввод — просто удаляем сообщение пользователя и оставляем режим ожидания
        await safe_delete_message(context, chat_id, user_msg_id)
        return

    symbol = str(awaiting_symbol).upper()
    anchor_price = float(value)

    # Загружаем или создаём конфиг для символа
    cfg = get_symbol_config(symbol)
    if not cfg:
        cfg = DCAConfigPerSymbol(symbol=symbol)
    # Для режима FIX сохраняем цену и явно проставляем режим
    cfg.anchor_price = anchor_price
    cfg.anchor_mode = "FIX"

    # Сохраняем конфиг
    upsert_symbol_config(cfg)


    # Удаляем сообщения ожидания и ввода
    await safe_delete_message(context, chat_id, user_msg_id)
    if waiting_message_id:
        await safe_delete_message(context, chat_id, waiting_message_id)

    # Сбрасываем состояние ожидания
    user_data.pop("await_state", None)
    user_data.pop("await_message_id", None)
    user_data.pop("anchor_symbol", None)

    # Тихое поведение: без сообщений "ANCHOR сохранен"
    # Просто перерисовываем MAIN MENU с учётом актуального подменю
    await redraw_main_menu_from_user_data(context)




async def handle_dca_anchor_ma30_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработка текста, когда ждём ввод offset для режима MA30."""
    message = update.message
    if not message:
        return

    chat_id = message.chat_id
    user_msg_id = message.message_id
    user_data = context.user_data

    raw = (message.text or "").strip()
    awaiting_symbol = user_data.get("anchor_symbol")
    waiting_message_id = user_data.get("await_message_id")

    # Проверяем, что у нас есть символ, для которого ждём offset
    if not awaiting_symbol:
        # Неизвестное состояние — просто чистим сообщение пользователя и выходим
        await safe_delete_message(context, chat_id, user_msg_id)
        if waiting_message_id:
            await safe_delete_message(context, chat_id, waiting_message_id)
        user_data.pop("await_state", None)
        user_data.pop("await_message_id", None)
        user_data.pop("anchor_symbol", None)
        return

    symbol = str(awaiting_symbol).upper()

    # Парсим offset: ABS или PCT
    txt = raw.strip().replace(",", ".")
    txt = txt.replace(" ", "")
    if not txt:
        await safe_delete_message(context, chat_id, user_msg_id)
        return

    is_pct = txt.endswith("%")
    if is_pct:
        num_part = txt[:-1]
        offset_type = "PCT"
    else:
        num_part = txt
        offset_type = "ABS"

    try:
        offset_value = float(num_part)
    except ValueError:
        # Некорректный ввод offset — удаляем сообщение пользователя, но ждём дальше
        await safe_delete_message(context, chat_id, user_msg_id)
        return

    # Загружаем или создаём конфиг
    cfg = get_symbol_config(symbol)
    if not cfg:
        cfg = DCAConfigPerSymbol(symbol=symbol)

    cfg.anchor_mode = "MA30"
    cfg.anchor_offset_type = offset_type
    cfg.anchor_offset_value = offset_value

    # Опциональный превью-anchor: берём MA30 из state и применяем offset
    preview_anchor = None
    try:
        state_path = Path(STORAGE_DIR) / f"{symbol}state.json"
        if state_path.exists():
            with state_path.open("r", encoding="utf-8") as f:
                state = json.load(f)
            ma30_val = state.get("MA30")
            if ma30_val is not None:
                base = float(ma30_val)
                if base > 0:
                    preview_anchor = apply_anchor_offset(base, offset_value, offset_type)
    except Exception:  # noqa: BLE001
        preview_anchor = None

    if preview_anchor is not None and preview_anchor > 0:
        cfg.anchor_price = preview_anchor

    upsert_symbol_config(cfg)

    # Удаляем сообщения ожидания и ввода
    await safe_delete_message(context, chat_id, user_msg_id)
    if waiting_message_id:
        await safe_delete_message(context, chat_id, waiting_message_id)

    # Сбрасываем состояние ожидания
    user_data.pop("await_state", None)
    user_data.pop("await_message_id", None)
    user_data.pop("anchor_symbol", None)

    # Остаёмся в подменю DCA/CONFIG, чтобы можно было сразу нажать ON
    user_data["current_menu"] = "dca_config"

    # Тихо перерисовываем MAIN MENU
    await redraw_main_menu_from_user_data(context)
    return


async def handle_dca_anchor_price_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработка текста, когда ждём ввод offset для режима PRICE."""
    message = update.message
    if not message:
        return

    chat_id = message.chat_id
    user_msg_id = message.message_id
    user_data = context.user_data

    raw = (message.text or "").strip()
    awaiting_symbol = user_data.get("anchor_symbol")
    waiting_message_id = user_data.get("await_message_id")

    # Проверяем, что у нас есть символ, для которого ждём offset
    if not awaiting_symbol:
        # Неизвестное состояние — просто чистим сообщение пользователя и выходим
        await safe_delete_message(context, chat_id, user_msg_id)
        if waiting_message_id:
            await safe_delete_message(context, chat_id, waiting_message_id)
        user_data.pop("await_state", None)
        user_data.pop("await_message_id", None)
        user_data.pop("anchor_symbol", None)
        return

    symbol = str(awaiting_symbol).upper()

    # Парсим offset: ABS или PCT
    txt = raw.strip().replace(",", ".")
    txt = txt.replace(" ", "")
    if not txt:
        await safe_delete_message(context, chat_id, user_msg_id)
        return

    is_pct = txt.endswith("%")
    if is_pct:
        num_part = txt[:-1]
        offset_type = "PCT"
    else:
        num_part = txt
        offset_type = "ABS"

    try:
        offset_value = float(num_part)
    except ValueError:
        # Некорректный ввод offset — удаляем сообщение пользователя, но ждём дальше
        await safe_delete_message(context, chat_id, user_msg_id)
        return

    # Загружаем или создаём конфиг
    cfg = get_symbol_config(symbol)
    if not cfg:
        cfg = DCAConfigPerSymbol(symbol=symbol)

    cfg.anchor_mode = "PRICE"
    cfg.anchor_offset_type = offset_type
    cfg.anchor_offset_value = offset_value

    # Опциональный превью-anchor: берём last price из state и применяем offset
    preview_anchor = None
    try:
        base = get_last_price_from_state(symbol)
        if base is not None and base > 0:
            preview_anchor = apply_anchor_offset(base, offset_value, offset_type)
    except Exception:  # noqa: BLE001
        preview_anchor = None

    if preview_anchor is not None and preview_anchor > 0:
        cfg.anchor_price = preview_anchor

    upsert_symbol_config(cfg)

    # Удаляем сообщения ожидания и ввода
    await safe_delete_message(context, chat_id, user_msg_id)
    if waiting_message_id:
        await safe_delete_message(context, chat_id, waiting_message_id)

    # Сбрасываем состояние ожидания
    user_data.pop("await_state", None)
    user_data.pop("await_message_id", None)
    user_data.pop("anchor_symbol", None)

    # Остаёмся в подменю DCA/CONFIG, чтобы можно было сразу нажать ON
    user_data["current_menu"] = "dca_config"

    # Тихо перерисовываем MAIN MENU
    await redraw_main_menu_from_user_data(context)
    return
async def handle_coins_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработка текста, когда ждём ввод монет после нажатия кнопки COINS."""
    message = update.message
    if not message:
        return

    chat_id = message.chat_id
    user_msg_id = message.message_id

    raw = (message.text or "").strip()
    coins = parse_coins_string(raw)
    waiting_message_id = context.user_data.get("await_message_id")

    if not coins:
        alert_text = (
            "Не удалось распознать ни одной монеты.\n"
            "Введите монеты через запятую, например: BTCUSDC, ETHUSDC"
        )
        await message.reply_text(alert_text, reply_markup=build_ok_alert_keyboard())
        await safe_delete_message(context, chat_id, user_msg_id)
        if waiting_message_id:
            await safe_delete_message(context, chat_id, waiting_message_id)
        context.user_data.pop("await_state", None)
        context.user_data.pop("await_message_id", None)
        return

    save_coins(coins)

    # Успешно сохранили список монет — тихо обновляем карточку без дополнительного сообщения
    await safe_delete_message(context, chat_id, user_msg_id)
    if waiting_message_id:
        await safe_delete_message(context, chat_id, waiting_message_id)

    context.user_data.pop("await_state", None)
    context.user_data.pop("await_message_id", None)

    # После изменения списка монет перерисовываем MAIN MENU
    await redraw_main_menu_from_user_data(context)


async def text_message_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Обработка любого текстового сообщения.

    Если ждём ввод монет (после кнопки COINS) — обрабатываем как ввод монет.
    Иначе: показываем подсказку и удаляем сообщение.
    """
    message = update.message
    if not message:
        return

    await_state = context.user_data.get("await_state")

    if await_state == "coins_input":
        await handle_coins_input(update, context)
        return

    if await_state == "dca_budget_input":
        await handle_dca_budget_input(update, context)
        return

    if await_state == "dca_levels_input":
        await handle_dca_levels_input(update, context)
        return

    if await_state == "dca_anchor_input":
        await handle_dca_anchor_input(update, context)
        return
    if await_state == "dca_anchor_ma30_input":
        await handle_dca_anchor_ma30_input(update, context)
        return
    if await_state == "dca_anchor_price_input":
        await handle_dca_anchor_price_input(update, context)
        return

    chat_id = message.chat_id
    message_id = message.message_id

    alert_text = "Используйте главное меню и кнопки для управления ботом."
    await message.reply_text(alert_text, reply_markup=build_ok_alert_keyboard())
    await safe_delete_message(context, chat_id, message_id)


# ---------- ГЛОБАЛЬНЫЙ ОБРАБОТЧИК ОШИБОК ----------


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Глобальный обработчик ошибок приложения."""
    err = context.error
    if isinstance(err, TimedOut):
        log.warning(
            "Глобальный обработчик: сетевой таймаут при работе с Telegram API: %s",
            err,
        )
    elif isinstance(err, NetworkError):
        log.warning(
            "Глобальный обработчик: сетевая ошибка при работе с Telegram API: %s",
            err,
        )
    else:
        log.exception(
            "Глобальный обработчик: необработанная ошибка: %s",
            err,
        )


# ---------- РЕГИСТРАЦИЯ ХЭНДЛЕРОВ ----------


def register_handlers(app: Application) -> None:
    """Регистрация всех командных и callback-хэндлеров."""
    # Базовые команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("coins", coins_cmd))
    app.add_handler(CommandHandler("metrics", metrics_cmd))
    app.add_handler(CommandHandler("rollover", rollover_cmd))
    app.add_handler(CommandHandler("dca", dca_cmd))

    # Главное меню
    app.add_handler(CommandHandler("menu", menu_cmd))

    # Стикер-меню
    app.add_handler(
        MessageHandler(
            filters.Sticker.ALL,
            sticker_menu,
        ),
    )

    # Callback-кнопки главного меню и подменю
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))

    # Кнопка OK для alert-сообщений
    app.add_handler(CallbackQueryHandler(alert_ok_callback, pattern=r"^alert:ok$"))

    # Любой произвольный текст (не команды)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_message_handler,
        ),
    )

    # Глобальный обработчик ошибок
    app.add_error_handler(error_handler)