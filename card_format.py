import os
from pathlib import Path

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from dca_status import build_dca_status_text


STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)


def build_symbol_card_text(symbol: str, storage_dir: str | None = None) -> str:
    """
    Собрать текст карточки для символа.

    Пока полностью совпадает с выводом /dca status <symbol>,
    который формирует build_dca_status_text в виде 8 строк
    с табами между колонками.
    """
    if storage_dir is None:
        storage_dir = STORAGE_DIR
    return build_dca_status_text(symbol, storage_dir=storage_dir)


def build_symbol_card_keyboard(symbol: str) -> InlineKeyboardMarkup:
    """
    Собрать inline-клавиатуру для карточки символа.

    Верхний ряд:
    [DCA] [ORDER] [LOGS] [MENU]
    """
    symbol = (symbol or "").upper()

    buttons_row = [
        InlineKeyboardButton(
            text="DCA",
            callback_data=f"card:dca:{symbol}",
        ),
        InlineKeyboardButton(
            text="ORDER",
            callback_data=f"card:order:{symbol}",
        ),
        InlineKeyboardButton(
            text="LOGS",
            callback_data=f"card:logs:{symbol}",
        ),
        InlineKeyboardButton(
            text="MENU",
            callback_data=f"card:menu:{symbol}",
        ),
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons_row])
    return keyboard
