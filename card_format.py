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


def _build_root_keyboard(symbol: str) -> InlineKeyboardMarkup:
    """
    Верхний уровень клавиатуры карточки:
    [DCA] [ORDER] [LOGS] [MENU]
    """
    symbol = (symbol or "").upper()

    row = [
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

    keyboard = InlineKeyboardMarkup(inline_keyboard=[row])
    return keyboard


def _build_dca_keyboard(symbol: str) -> InlineKeyboardMarkup:
    """
    Подменю для DCA:
    [CONFIG] [RUN] [STATUS]
    [↩️]

    ↩️ возвращает на предыдущий уровень (в нашем случае — на корневое меню).
    """
    symbol = (symbol or "").upper()

    row1 = [
        InlineKeyboardButton(
            text="CONFIG",
            callback_data=f"card:dca_cfg:{symbol}",
        ),
        InlineKeyboardButton(
            text="RUN",
            callback_data=f"card:dca_run:{symbol}",
        ),
        InlineKeyboardButton(
            text="STATUS",
            callback_data=f"card:dca_status:{symbol}",
        ),
    ]

    row2 = [
        InlineKeyboardButton(
            text="↩️",
            callback_data=f"card:back_root:{symbol}",
        )
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[row1, row2])
    return keyboard


def build_symbol_card_keyboard(symbol: str, menu: str = "root") -> InlineKeyboardMarkup:
    """
    Построить клавиатуру для карточки с учётом уровня меню.

    menu:
      - "root" — верхний уровень (DCA / ORDER / LOGS / MENU)
      - "dca"  — подменю DCA (CONFIG / RUN / STATUS / ↩️)
    """
    if menu == "dca":
        return _build_dca_keyboard(symbol)
    return _build_root_keyboard(symbol)
