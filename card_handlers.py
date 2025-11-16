import json
import os
from pathlib import Path

from aiogram import Router, types, F
from aiogram.filters import Command

from card_format import build_symbol_card_text, build_symbol_card_keyboard


router = Router()

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)


def _load_symbols_list() -> list[str] | None:
    """
    Загрузить список символов из STORAGE_DIR/symbols_list.json.

    Ожидаемый формат файла:
    {
      "symbols": ["BNBUSDC", "BTCUSDC", ...]
    }

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


@router.message(Command("card"))
async def cmd_card(message: types.Message) -> None:
    """
    Карточка по символу: /card <symbol>.

    Пока показывает тот же текст, что и /dca status <symbol>,
    но в табличном формате + inline-клавиатура.
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
    keyboard = build_symbol_card_keyboard(symbol)

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
    где <action> ∈ {dca, order, logs, menu}.
    """
    data = callback.data or ""
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    symbol = parts[2] if len(parts) > 2 else ""
    action = action.lower()

    if action == "dca":
        await callback.answer(
            f"DCA-настройки для {symbol} будут добавлены позже.",
            show_alert=False,
        )
    elif action == "order":
        await callback.answer(
            f"Модуль ордеров для {symbol} ещё в разработке.",
            show_alert=False,
        )
    elif action == "logs":
        await callback.answer(
            f"Просмотр логов для {symbol} появится на следующих шагах.",
            show_alert=False,
        )
    elif action == "menu":
        await callback.answer(
            "Меню карточки будет расширено на следующих шагах.",
            show_alert=False,
        )
    else:
        await callback.answer("Неизвестное действие карточки.", show_alert=False)
