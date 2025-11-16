import json
import os
import time
from pathlib import Path

from aiogram import Router, types, F
from aiogram.filters import Command

from card_format import build_symbol_card_text, build_symbol_card_keyboard
from dca_config import get_symbol_config, upsert_symbol_config, save_dca_config, load_dca_config
from dca_models import DCAConfigPerSymbol


router = Router()

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)

# Маппинг всех известных ID стикеров (основные файлы + превью) на символ.
STICKER_ID_TO_SYMBOL: dict[str, str] = {
    # BNBUSDC
    "CAACAgIAAxkBAAE9djtpDD842Hiibb4OWsspe5QgYvQsgwACJocAAka7YUijem2oBO1AazYE": "BNBUSDC",
    "AgADJocAAka7YUg": "BNBUSDC",
    "AAMCAgADGQEAAT12O2kMPzjYeKJtvg5ayyl7lCBi9CyDAAImhwACRrthSKN6bagE7UBrAQAHbQADNgQ": "BNBUSDC",
    "AQADJocAAka7YUhy": "BNBUSDC",

    # ETHUSDC
    "CAACAgIAAxkBAAE9ddhpDCyOcuY8oEj0_mPe_E1zbEa-ogACxokAAv_wWEir8uUsEqgkvDYE": "ETHUSDC",
    "AgADxokAAv_wWEg": "ETHUSDC",
    "AAMCAgADGQEAAT112GkMLI5y5jygSPT-Y978TXNsRr6iAALGiQAC__BYSKvy5SwSqCS8AQAHbQADNgQ": "ETHUSDC",
    "AQADxokAAv_wWEhy": "ETHUSDC",

    # BTCUSDC
    "CAACAgIAAxkBAAE9dPtpDAnY_j75m55h8ctPgwzLP4fy8gACJogAAtfnYUiiLR_pVyWZPTYE": "BTCUSDC",
    "AgADJogAAtfnYUg": "BTCUSDC",
    "AAMCAgADGQEAAT10-2kMCdj-PvmbnmHxy0-DDMs_h_LyAAImiAAC1-dhSKItH-lXJZk9AQAHbQADNgQ": "BTCUSDC",
    "AQADJogAAtfnYUhy": "BTCUSDC",
}


# Простое состояние ожидания ввода бюджета по чату.
# Ключ: chat_id, значение: словарь с symbol.
_WAITING_BUDGET: dict[int, dict] = {}


def _load_symbols_list() -> list[str] | None:
    """
    Загрузить список символов из STORAGE_DIR/symbols_list.json.

    Ожидаемый формат файла:
    {
      "symbols": ["BNBUSDC", "BTCUSDC", ...]
    }

    Либо просто список строк:
    ["BNBUSDC", "BTCUSDC", ...]

    Возвращает список символов в верхнем регистре без дубликатов.
    Если файл отсутствует или повреждён — возвращает None.
    """
    path = STORAGE_PATH / "symbols_list.json"
    if not path.exists():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return None

    symbols: list[str] = []

    # Разрешим два варианта формата:
    # 1) словарь с ключом "symbols"
    # 2) просто список строк
    if isinstance(data, dict):
        src = data.get("symbols", [])
    else:
        src = data

    if not isinstance(src, list):
        return None

    for item in src:
        if not isinstance(item, str):
            continue
        s = item.strip().upper()
        if not s:
            continue
        if s not in symbols:
            symbols.append(s)

    return symbols


def _extract_sticker_ids(sticker: types.Sticker) -> set[str]:
    """Собрать все доступные ID стикера и его превью (file_id и file_unique_id)."""
    ids: set[str] = set()

    for attr in ("file_id", "file_unique_id"):
        val = getattr(sticker, attr, None)
        if val:
            ids.add(val)

    for sub_name in ("thumb", "thumbnail"):
        sub = getattr(sticker, sub_name, None)
        if not sub:
            continue
        for attr in ("file_id", "file_unique_id"):
            val = getattr(sub, attr, None)
            if val:
                ids.add(val)

    return ids


def _grid_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}_grid.json"


def _has_active_campaign(symbol: str) -> bool:
    """
    Проверить, есть ли активная кампания для символа.

    Активная кампания = есть файл SYMBOL_grid.json и в нём нет campaign_end_ts.
    """
    symbol = (symbol or "").upper()
    path = _grid_path(symbol)
    if not path.exists():
        return False

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return False

    return not bool(data.get("campaign_end_ts"))


@router.message(Command("card"))
async def cmd_card(message: types.Message) -> None:
    """
    Карточка по символу: /card <symbol>.

    Показывает тот же текст, что и /dca status <symbol>,
    но в табличном формате + inline-клавиатура (верхний уровень).
    """
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /card <symbol>")
        return

    symbol = (parts[1] or "").strip().upper()
    if not symbol:
        await message.answer("Использование: /card <symbol>")
        return

    symbols = _load_symbols_list()
    # Если файл есть и в нём что-то есть — проверяем наличие символа.
    if symbols is not None and symbols and symbol not in symbols:
        await message.answer(f"Символ {symbol} отсутствует в symbols_list.json.")
        return

    text_block = build_symbol_card_text(symbol, storage_dir=STORAGE_DIR)
    keyboard = build_symbol_card_keyboard(symbol, menu="root")

    await message.answer(
        f"<pre>{text_block}</pre>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@router.message(F.sticker)
async def on_card_sticker(message: types.Message) -> None:
    """
    Привязка стикеров к карточкам.

    Если пришёл один из наших стикеров, показываем соответствующую /card <symbol>.
    Остальные стикеры игнорируем (не мешаем другим хэндлерам).
    """
    sticker = message.sticker
    if not sticker:
        return

    all_ids = _extract_sticker_ids(sticker)
    symbol: str | None = None
    for sid in all_ids:
        symbol = STICKER_ID_TO_SYMBOL.get(sid)
        if symbol:
            break

    if not symbol:
        # Не наш стикер — выходим.
        return

    symbol = symbol.upper()

    text_block = build_symbol_card_text(symbol, storage_dir=STORAGE_DIR)
    keyboard = build_symbol_card_keyboard(symbol, menu="root")

    await message.answer(
        f"<pre>{text_block}</pre>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("card:"))
async def on_card_callback(callback: types.CallbackQuery) -> None:
    """
    Обработка нажатий на кнопки карточки /card.

    Формат callback_data: "card:<action>:<symbol>"

    Верхний уровень:
      - "card:dca:<symbol>"      → открыть подменю DCA
      - "card:order:<symbol>"    → заглушка (пока)
      - "card:logs:<symbol>"     → заглушка (пока)
      - "card:menu:<symbol>"     → заглушка (пока)

    Подменю DCA (уровень 1):
      - "card:dca_cfg:<symbol>"     → открыть CONFIG-меню
      - "card:dca_run:<symbol>"     → открыть RUN-меню
      - "card:dca_status:<symbol>"  → открыть STATUS-меню
      - "card:back_root:<symbol>"   → ↩️ назад на верхний уровень

    Подменю CONFIG:
      - "card:dca_cfg_budget:<symbol>"  → BUDGET
      - "card:dca_cfg_levels:<symbol>"  → LEVELS (заглушка)
      - "card:dca_cfg_list:<symbol>"    → LIST (заглушка)
      - "card:back_dca:<symbol>"        → ↩️ назад в DCA-меню

    Подменю RUN:
      - "card:dca_run_start:<symbol>"   → START (заглушка)
      - "card:dca_run_stop:<symbol>"    → STOP (заглушка)
      - "card:back_dca:<symbol>"        → ↩️ назад в DCA-меню

    Подменю STATUS:
      - "card:dca_status_all:<symbol>"    → ALL (заглушка)
      - "card:dca_status_active:<symbol>" → ACTIVE (заглушка)
      - "card:back_dca:<symbol>"          → ↩️ назад в DCA-меню
    """
    data = callback.data or ""
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    symbol = parts[2] if len(parts) > 2 else ""
    symbol = (symbol or "").upper()
    action = action.lower()

    chat_id = callback.message.chat.id if callback.message else None

    # ---------- Верхний уровень ----------
    if action == "dca":
        kb = build_symbol_card_keyboard(symbol, menu="dca")
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await callback.answer()
        return

    if action == "order":
        await callback.answer(
            f"Модуль ордеров для {symbol} ещё в разработке.",
            show_alert=False,
        )
        return

    if action == "logs":
        await callback.answer(
            f"Просмотр логов для {symbol} появится на следующих шагах.",
            show_alert=False,
        )
        return

    if action == "menu":
        await callback.answer(
            "Меню карточки будет расширено на следующих шагах.",
            show_alert=False,
        )
        return

    # ---------- Подменю DCA, уровень 1 ----------
    if action == "dca_cfg":
        kb = build_symbol_card_keyboard(symbol, menu="dca_config")
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await callback.answer()
        return

    if action == "dca_run":
        kb = build_symbol_card_keyboard(symbol, menu="dca_run")
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await callback.answer()
        return

    if action == "dca_status":
        kb = build_symbol_card_keyboard(symbol, menu="dca_status")
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await callback.answer()
        return

    if action == "back_root":
        kb = build_symbol_card_keyboard(symbol, menu="root")
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await callback.answer()
        return

    # ---------- Подменю CONFIG ----------
    if action == "dca_cfg_budget":
        # Нельзя менять бюджет при активной кампании.
        if _has_active_campaign(symbol):
            await callback.answer(
                f"Нельзя менять BUDGET для {symbol} при активной кампании.\n"
                "Сначала остановите кампанию.",
                show_alert=True,
            )
            return

        # Готовимся принять число от пользователя.
        if chat_id is not None:
            _WAITING_BUDGET[chat_id] = {"symbol": symbol}

        cfg = get_symbol_config(symbol)
        current_budget = cfg.budget_usdc if cfg else 0
        await callback.message.answer(
            f"Введи новый BUDGET для {symbol} в USDC.
"
            f"Текущий бюджет: {current_budget}",
        )
        await callback.answer()
        return

    if action == "dca_cfg_levels":
        await callback.answer(
            f"CONFIG/LEVELS для {symbol} будет добавлен позже.",
            show_alert=False,
        )
        return

    if action == "dca_cfg_list":
        await callback.answer(
            f"CONFIG/LIST для {symbol} будет добавлен позже.",
            show_alert=False,
        )
        return

    # ---------- Подменю RUN ----------
    if action == "dca_run_start":
        await callback.answer(
            f"RUN/START для {symbol} будет добавлен позже.",
            show_alert=False,
        )
        return

    if action == "dca_run_stop":
        await callback.answer(
            f"RUN/STOP для {symbol} будет добавлен позже.",
            show_alert=False,
        )
        return

    # ---------- Подменю STATUS ----------
    if action == "dca_status_all":
        await callback.answer(
            f"STATUS/ALL для {symbol} будет добавлен позже.",
            show_alert=False,
        )
        return

    if action == "dca_status_active":
        await callback.answer(
            f"STATUS/ACTIVE для {symbol} будет добавлен позже.",
            show_alert=False,
        )
        return

    # ---------- Возврат в DCA-меню из подменю ----------
    if action == "back_dca":
        kb = build_symbol_card_keyboard(symbol, menu="dca")
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await callback.answer()
        return

    # На всякий случай — дефолт
    await callback.answer("Неизвестное действие карточки.", show_alert=False)


@router.message(F.text)
async def on_text_for_budget(message: types.Message) -> None:
    """
    Обработка текстового ввода после нажатия CONFIG → BUDGET.

    Ждём число, обновляем budget_usdc в конфиге и перевыдаём карточку
    на том же уровне меню (CONFIG).
    """
    chat_id = message.chat.id
    ctx = _WAITING_BUDGET.get(chat_id)
    if not ctx:
        # Ничего не ждём — пропускаем сообщение дальше по цепочке хэндлеров.
        return

    symbol = (ctx.get("symbol") or "").upper()
    if not symbol:
        _WAITING_BUDGET.pop(chat_id, None)
        return

    text = (message.text or "").strip()

    # Пытаемся распарсить число.
    try:
        new_budget = float(text.replace(",", "."))
    except Exception:
        await message.answer(
            "Некорректное значение бюджета. Введи число, например 300 или 150.5."
        )
        return

    if new_budget <= 0:
        await message.answer("Бюджет должен быть больше нуля.")
        return

    # На всякий случай ещё раз проверим активную кампанию.
    if _has_active_campaign(symbol):
        _WAITING_BUDGET.pop(chat_id, None)
        await message.answer(
            f"Нельзя менять BUDGET для {symbol} при активной кампании.
"
            f"Сначала остановите кампанию."
        )
        return

    # Обновляем конфиг так же, как в /dca set <symbol> budget <B>.
    cfg = get_symbol_config(symbol)
    if not cfg:
        cfg = DCAConfigPerSymbol(
            symbol=symbol,
            budget_usdc=new_budget,
            levels=0,
            enabled=True,
        )
    else:
        cfg.budget_usdc = new_budget

    cfg.updated_ts = int(time.time())
    upsert_symbol_config(cfg)
    save_dca_config(load_dca_config())

    # Выходим из режима ожидания.
    _WAITING_BUDGET.pop(chat_id, None)

    # Перевыдаём карточку символа с обновлённым бюджетом.
    text_block = build_symbol_card_text(symbol, storage_dir=STORAGE_DIR)
    keyboard = build_symbol_card_keyboard(symbol, menu="dca_config")

    await message.answer(
        f"<pre>{text_block}</pre>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
