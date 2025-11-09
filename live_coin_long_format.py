from typing import Dict

from coin_long_format import build_long_card as _base_build_long_card


def build_long_card(data: Dict) -> str:
    """
    LIVE-версия карточки LONG.

    Пока полностью повторяет виртуальную карточку, но помечается LIVE=✅.
    В дальнейшем сюда можно будет добавить реальные объёмы, лоты и т.п.
    """
    return _base_build_long_card(data, is_live=True)
