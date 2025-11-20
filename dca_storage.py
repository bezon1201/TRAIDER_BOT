from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from config import STORAGE_DIR
from dca_models import DCAStatePerSymbol

STORAGE_PATH = Path(STORAGE_DIR)
GRID_LOG_PATH = STORAGE_PATH / "grid_log.jsonl"


def _ensure_storage_dir() -> None:
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)


def grid_state_path(symbol: str) -> Path:
    """Путь к файлу состояния сетки для symbol."""
    symbol = symbol.upper()
    return STORAGE_PATH / f"{symbol}_grid.json"


def load_grid_state(symbol: str) -> Optional[DCAStatePerSymbol]:
    """Загрузить состояние DCA-сетки для symbol, если оно существует."""
    path = grid_state_path(symbol)
    if not path.exists():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw or "{}")
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    try:
        return DCAStatePerSymbol.from_dict(data)
    except Exception:
        return None


def save_grid_state(symbol: str, state: DCAStatePerSymbol) -> None:
    """Сохранить состояние DCA-сетки для symbol."""
    _ensure_storage_dir()
    path = grid_state_path(symbol)
    data = state.to_dict()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_grid_log(record: Dict[str, Any]) -> None:
    """Добавить запись в журнал grid_log.jsonl.

    Каждая строка — отдельный JSON-объект.
    """
    _ensure_storage_dir()
    rec = dict(record or {})
    rec.setdefault("ts", int(time.time()))
    try:
        line = json.dumps(rec, ensure_ascii=False)
    except Exception:
        return

    with GRID_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
