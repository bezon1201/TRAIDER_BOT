import json
import os
from pathlib import Path

from aiogram import Router, types, F
from aiogram.filters import Command

from card_format import build_symbol_card_text, build_symbol_card_keyboard


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
    keyboard = build_symbol_card_keyboard(symbol, menu="root")  # верхний уровень

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
    keyboard = build_symbol_card_keyboard(symbol, menu="root")  # верхний уровень

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

    Подменю DCA:
      - "card:dca_cfg:<symbol>"    → CONFIG (заглушка)
      - "card:dca_run:<symbol>"    → RUN (заглушка)
      - "card:dca_status:<symbol>" → STATUS (заглушка)
      - "card:back_root:<symbol>"  → ↩️ вернуться на верхний уровень
    """
    data = callback.data or ""
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    symbol = parts[2] if len(parts) > 2 else ""
    symbol = (symbol or "").upper()
    action = action.lower()

    # DCA → открыть подменю (CONFIG / RUN / STATUS / ↩️)
    if action == "dca":
        kb = build_symbol_card_keyboard(symbol, menu="dca")
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await callback.answer()
        return

    # Подменю DCA — заглушки действий
    if action == "dca_cfg":
        await callback.answer(
            f"CONFIG для {symbol} будет добавлен позже.",
            show_alert=False,
        )
        return

    if action == "dca_run":
        await callback.answer(
            f"RUN для {symbol} будет добавлен позже.",
            show_alert=False,
        )
        return

    if action == "dca_status":
        await callback.answer(
            f"STATUS для {symbol} будет добавлен позже.",
            show_alert=False,
        )
        return

    # ↩️ — вернуться на верхний уровень меню
    if action == "back_root":
        kb = build_symbol_card_keyboard(symbol, menu="root")
        try:
            await callback.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            pass
        await callback.answer()
        return

    # Остальные верхнеуровневые кнопки пока как заглушки
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

    # На всякий случай — дефолт
    await callback.answer("Неизвестное действие карточки.", show_alert=False)
