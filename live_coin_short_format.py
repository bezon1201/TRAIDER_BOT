from typing import Dict

from coin_short_format import build_short_card as _base_build_short_card


def build_short_card(data: Dict) -> str:
    """
    LIVE-версия карточки SHORT.

    Пока полностью повторяет виртуальную карточку, но помечается LIVE=✅.
    В дальнейшем сюда можно будет добавить реальные объёмы, лоты и т.п.
    """
    return _base_build_short_card(data, is_live=True)
