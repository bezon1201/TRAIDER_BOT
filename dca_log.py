from __future__ import annotations

import json
import os
import time
import logging
from typing import Any, Dict, Literal, Optional, List

from config import STORAGE_DIR

log = logging.getLogger(__name__)

ReasonType = Literal["manual", "scheduler"]


def _log_path(symbol: str) -> str:
    """Путь к файлу логов DCA для символа.

    Формат: <STORAGE_DIR>/<SYMBOL>_dca_log.jsonl
    """
    filename = f"{symbol.upper()}_dca_log.jsonl"
    return os.path.join(STORAGE_DIR, filename)


def log_dca_event(
    symbol: str,
    event: str,
    *,
    grid_id: Optional[int] = None,
    reason: ReasonType,
    **extra: Any,
) -> None:
    """Записать одно DCA-событие в jsonl-лог для символа.

    Примеры event:
    - "grid_created"
    - "orders_created"
    - "campaign_started"
    - "campaign_stopped"

    reason:
    - "manual"    — событие инициировано вручную (команда, кнопка)
    - "scheduler" — событие инициировано планировщиком/фоновым процессом
    """
    symbol_u = (symbol or "").upper()
    if not symbol_u:
        return

    payload: Dict[str, Any] = {
        "ts": time.time(),
        "event": event,
        "symbol": symbol_u,
        "reason": reason,
    }

    if grid_id is not None:
        payload["grid_id"] = grid_id

    if extra:
        payload.update(extra)

    path = _log_path(symbol_u)
    try:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.write("\n")
    except OSError as e:  # noqa: BLE001
        log.exception("Не удалось записать DCA-лог для %s: %s", symbol_u, e)


def read_dca_events(symbol: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Прочитать DCA-события для символа из jsonl-лога.

    Если limit задан — вернуть не более limit последних событий.
    Функция предназначена для отладки и будущего UI, а не для
    критичной бизнес-логики.
    """
    path = _log_path(symbol)
    if not os.path.exists(path):
        return []

    events: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.append(evt)
    except OSError as e:  # noqa: BLE001
        log.exception("Не удалось прочитать DCA-лог для %s: %s", symbol, e)
        return []

    if limit is None or limit <= 0:
        return events

    return events[-limit:]
