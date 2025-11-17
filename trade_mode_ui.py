"""
Вспомогательные структуры для связи между кнопками карточек (MENU/MODE)
и логикой смены режима торговли в trade_mode.py.

Храним минимальный контекст callback'а, чтобы после успешной смены режима:
- показать toast через answer_callback_query;
- пересобрать карточку для конкретного символа.
"""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class PendingModeChangeFromCard:
    chat_id: int
    callback_query_id: str
    symbol: str
    message_chat_id: int
    message_id: int


_pending_by_chat: Dict[int, PendingModeChangeFromCard] = {}


def set_pending_from_card(
    chat_id: int,
    callback_query_id: str,
    symbol: str,
    message_chat_id: int,
    message_id: int,
) -> None:
    """Сохранить ожидание смены режима для конкретного чата (админа)."""
    global _pending_by_chat
    _pending_by_chat[chat_id] = PendingModeChangeFromCard(
        chat_id=chat_id,
        callback_query_id=callback_query_id,
        symbol=symbol,
        message_chat_id=message_chat_id,
        message_id=message_id,
    )


def pop_pending_for_chat(chat_id: int) -> Optional[PendingModeChangeFromCard]:
    """Забрать и удалить ожидание смены режима для чата, если оно есть."""
    global _pending_by_chat
    return _pending_by_chat.pop(chat_id, None)
