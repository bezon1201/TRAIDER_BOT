import json
import os
import time
from pathlib import Path

from aiogram import Router, types, F
from aiogram.filters import Command

from card_format import build_symbol_card_text, build_symbol_card_keyboard
from dca_status import build_dca_status_text
from dca_handlers import (
    get_symbol_min_notional,
    _load_state_for_symbol,
    _build_grid_for_symbol,
    _format_money,
)
from dca_config import get_symbol_config, upsert_symbol_config, save_dca_config, load_dca_config, validate_budget_vs_min_notional, zero_symbol_budget
from dca_models import DCAConfigPerSymbol
from grid_log import log_grid_created, log_grid_manualy_closed


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
_WAITING_LEVELS: dict[int, dict] = {}


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
            f"Введи новый BUDGET для {symbol} в USDC.\n"
            f"Текущий бюджет: {current_budget}",
        )
        await callback.answer()
        return

    if action == "dca_cfg_levels":
        # Нельзя менять levels при активной кампании.
        if _has_active_campaign(symbol):
            await callback.answer(
                f"Нельзя менять LEVELS для {symbol} при активной кампании.\n"
                "Сначала остановите кампанию.",
                show_alert=True,
            )
            return

        # Готовимся принять число от пользователя.
        if chat_id is not None:
            _WAITING_LEVELS[chat_id] = {"symbol": symbol}

        cfg = get_symbol_config(symbol)
        current_levels = int(getattr(cfg, "levels_count", 0) or 0) if cfg else 0
        await callback.message.answer(
            f"Введи новое количество LEVELS для {symbol}.\n"
            f"Текущее значение: {current_levels}",
        )
        await callback.answer()
        return

    if action == "dca_cfg_list":
        # Аналог /dca list, но показываем в alert
        cfg_map = load_dca_config()
        if not cfg_map:
            await callback.answer(
                "DCA-конфиги пока не заданы. Добавьте символы через /dca config.",
                show_alert=True,
            )
            return

        lines: list[str] = ["Список DCA-конфигов:"]
        for sym, cfg in sorted(cfg_map.items()):
            min_notional = get_symbol_min_notional(sym)
            note = ""
            if min_notional > 0:
                ok, err = validate_budget_vs_min_notional(cfg, min_notional)
                if ok:
                    note = "OK"
                else:
                    note = f"ERR: {err}"
            lines.append(
                f"{sym}: budget={cfg.budget_usdc}$, "
                f"levels={cfg.levels_count}, "
                f"check={note or '-'}",
            )

        text = "\n".join(lines)
        await callback.answer(text, show_alert=True)
        return

    # ---------- Подменю RUN ----------
    if action == "dca_run_start":
        # Нажатие кнопки START в подменю RUN.
        # Логика аналогична /dca start <symbol>, но:
        # - ошибки показываем через alert с кнопкой OK
        # - при успехе даём короткий toast и перевыдаём карточку в том же меню.
        if not symbol:
            await callback.answer("DCA: символ не определён.", show_alert=True)
            return

        cfg = get_symbol_config(symbol)
        if cfg is None:
            await callback.answer(
                f"DCA: конфиг для {symbol} не найден. Сначала задайте его через /dca set.",
                show_alert=True,
            )
            return

        state = _load_state_for_symbol(symbol)
        if not state:
            await callback.answer(
                f"DCA: state для {symbol} не найден. Сначала выполните /now и /market для этой пары.",
                show_alert=True,
            )
            return

        min_notional = get_symbol_min_notional(symbol)
        if min_notional <= 0:
            await callback.answer(
                "DCA: minNotional для пары не удалось определить. "
                "Сначала выполните /now для этой пары, чтобы Binance-лимиты были загружены.",
                show_alert=True,
            )
            return

        ok, err = validate_budget_vs_min_notional(cfg, min_notional)
        if not ok:
            # Сообщение для alert должно быть коротким, иначе Telegram вернёт MESSAGE_TOO_LONG.
            msg = (
                f"DCA: бюджет для {symbol} слишком мал относительно minNotional.\n"
                f"budget={cfg.budget_usdc}$, levels={cfg.levels_count}, minNotional={min_notional}"
            )
            # На всякий случай отрежем слишком длинный текст.
            if len(msg) > 180:
                msg = msg[:177] + "..."
            await callback.answer(msg, show_alert=True)
            return

        # Фактическая генерация сетки
        try:
            grid = _build_grid_for_symbol(symbol, cfg, state)
        except ValueError as e:
            await callback.answer(
                f"DCA: не удалось построить сетку для {symbol}: {e}",
                show_alert=True,
            )
            return

        gpath = _grid_path(symbol)
        try:
            gpath.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            with gpath.open("w", encoding="utf-8") as f:
                json.dump(grid, f, ensure_ascii=False, indent=2)
        except Exception as e:
            await callback.answer(
                f"DCA: не удалось сохранить файл сетки для {symbol}: {e}",
                show_alert=True,
            )
            return

        # Логируем создание сетки
        try:
            log_grid_created(grid)
        except Exception:
            pass

        # Короткое уведомление (toast) об успешном старте
        await callback.answer(
            f"DCA start: сетка для {symbol} создана.",
            show_alert=False,
        )

        # Перевыдаём карточку и остаёмся в подменю RUN
        if callback.message:
            text_block = build_symbol_card_text(symbol, storage_dir=STORAGE_DIR)
            keyboard = build_symbol_card_keyboard(symbol, menu="dca_run")
            try:
                await callback.message.edit_text(
                    f"<pre>{text_block}</pre>",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            except Exception:
                # Fallback: отправим новую карточку, если не удалось отредактировать старую.
                await callback.message.answer(
                    f"<pre>{text_block}</pre>",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
        return

    if action == "dca_run_stop":
        # Нажатие кнопки STOP в подменю RUN.
        # Поведение:
        # - при ошибке показываем alert с кнопкой OK
        # - при успехе даём короткий toast и перевыдаём карточку в подменю RUN.
        if not symbol:
            await callback.answer("DCA: символ не определён.", show_alert=True)
            return

        gpath = _grid_path(symbol)
        try:
            raw = gpath.read_text(encoding="utf-8")
            grid = json.loads(raw)
        except Exception:
            await callback.answer(f"DCA: сетка для {symbol} не найдена.", show_alert=True)
            return

        if grid.get("campaign_end_ts"):
            await callback.answer(f"DCA: кампания для {symbol} уже завершена.", show_alert=True)
            return

        now_ts = int(time.time())
        grid["campaign_end_ts"] = now_ts
        grid["updated_ts"] = now_ts
        grid["closed_reason"] = "manual"

        try:
            with gpath.open("w", encoding="utf-8") as f:
                json.dump(grid, f, ensure_ascii=False, indent=2)
        except Exception as e:
            msg = f"DCA: не удалось обновить файл сетки для {symbol}: {e}"
            if len(msg) > 180:
                msg = msg[:177] + "..."
            await callback.answer(msg, show_alert=True)
            return

        # Обнуляем бюджет в DCA-конфиге для безопасности
        try:
            zero_symbol_budget(symbol)
        except Exception:
            pass

        # Логируем ручное закрытие кампании
        try:
            log_grid_manualy_closed(grid)
        except Exception:
            pass

        # Короткое уведомление (toast) об успешной остановке
        await callback.answer(
            f"DCA: кампания для {symbol} остановлена.",
            show_alert=False,
        )

        # Перевыдаём карточку и остаёмся в подменю RUN
        if callback.message:
            text_block = build_symbol_card_text(symbol, storage_dir=STORAGE_DIR)
            keyboard = build_symbol_card_keyboard(symbol, menu="dca_run")
            try:
                await callback.message.edit_text(
                    f"<pre>{text_block}</pre>",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            except Exception:
                await callback.message.answer(
                    f"<pre>{text_block}</pre>",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
        return

    # ---------- Подменю STATUS ----------
    if action == "dca_status_all":
        # Аналог /dca status all — все кампании, для которых есть сетка.
        storage = STORAGE_PATH
        count = 0
        for path in sorted(storage.glob("*_grid.json")):
            try:
                raw = path.read_text(encoding="utf-8")
                grid = json.loads(raw)
            except Exception:
                continue

            symbol_from_file = str(grid.get("symbol") or path.name.replace("_grid.json", "")).upper()
            text_block = build_dca_status_text(symbol_from_file, storage_dir=STORAGE_DIR)
            # Статусы могут быть длинными, поэтому выводим отдельными сообщениями, а не через alert.
            if callback.message:
                await callback.message.answer(
                    f"<pre>{text_block}</pre>",
                    parse_mode="HTML",
                )
            count += 1

        if count == 0 and callback.message:
            await callback.message.answer("DCA: кампаний (сеток) не найдено.")

        # Короткий toast, чтобы закрыть спиннер.
        await callback.answer("STATUS: ALL", show_alert=False)
        return

    if action == "dca_status_active":
        # Аналог /dca status active — только активные кампании (без campaign_end_ts).
        storage = STORAGE_PATH
        count = 0
        for path in sorted(storage.glob("*_grid.json")):
            try:
                raw = path.read_text(encoding="utf-8")
                grid = json.loads(raw)
            except Exception:
                continue

            if grid.get("campaign_end_ts"):
                # Уже завершена — пропускаем.
                continue

            symbol_from_file = str(grid.get("symbol") or path.name.replace("_grid.json", "")).upper()
            text_block = build_dca_status_text(symbol_from_file, storage_dir=STORAGE_DIR)
            if callback.message:
                await callback.message.answer(
                    f"<pre>{text_block}</pre>",
                    parse_mode="HTML",
                )
            count += 1

        if count == 0 and callback.message:
            await callback.message.answer("DCA: активных кампаний не найдено.")

        await callback.answer("STATUS: ACTIVE", show_alert=False)
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



@router.message(F.text, ~F.text.startswith("/"))
async def on_text_for_config_inputs(message: types.Message) -> None:
    """
    Обработка текстового ввода после нажатия CONFIG → BUDGET или CONFIG → LEVELS.

    Если мы не ждём никакого ввода, сообщение просто передаётся дальше.
    """
    chat_id = message.chat.id

    ctx_budget = _WAITING_BUDGET.get(chat_id)
    ctx_levels = _WAITING_LEVELS.get(chat_id)

    if not ctx_budget and not ctx_levels:
        # Ничего не ждём — пропускаем сообщение дальше по цепочке хэндлеров.
        return

    text = (message.text or "").strip()

    # ----- Режим ввода BUDGET -----
    if ctx_budget is not None:
        symbol = (ctx_budget.get("symbol") or "").upper()
        if not symbol:
            _WAITING_BUDGET.pop(chat_id, None)
            return

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
                f"Нельзя менять BUDGET для {symbol} при активной кампании.\n"
                "Сначала остановите кампанию.",
            )
            return

        # Обновляем конфиг так же, как в /dca set <symbol> budget <B>.
        cfg = get_symbol_config(symbol)
        if not cfg:
            cfg = DCAConfigPerSymbol(
                symbol=symbol,
                budget_usdc=new_budget,
                levels_count=0,
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
        return

    # ----- Режим ввода LEVELS -----
    if ctx_levels is not None:
        symbol = (ctx_levels.get("symbol") or "").upper()
        if not symbol:
            _WAITING_LEVELS.pop(chat_id, None)
            return

        try:
            new_levels = int(text)
        except Exception:
            await message.answer(
                "Некорректное значение LEVELS. Введи целое число, например 10."
            )
            return

        if new_levels <= 0:
            await message.answer("LEVELS должно быть положительным.")
            return

        # Проверяем активную кампанию.
        if _has_active_campaign(symbol):
            _WAITING_LEVELS.pop(chat_id, None)
            await message.answer(
                f"Нельзя менять LEVELS для {symbol} при активной кампании.\n"
                "Сначала остановите кампанию.",
            )
            return

        # Обновляем конфиг так же, как в /dca set <symbol> levels <N>.
        cfg = get_symbol_config(symbol)
        if not cfg:
            cfg = DCAConfigPerSymbol(
                symbol=symbol,
                budget_usdc=0.0,
                levels_count=new_levels,
                enabled=True,
            )
        else:
            cfg.levels_count = new_levels

        cfg.updated_ts = int(time.time())
        upsert_symbol_config(cfg)
        save_dca_config(load_dca_config())

        # Выходим из режима ожидания.
        _WAITING_LEVELS.pop(chat_id, None)

        # Перевыдаём карточку символа с обновлённым количеством уровней.
        text_block = build_symbol_card_text(symbol, storage_dir=STORAGE_DIR)
        keyboard = build_symbol_card_keyboard(symbol, menu="dca_config")

        await message.answer(
            f"<pre>{text_block}</pre>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
