
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from dca_models import DCAStatePerSymbol

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)
GRID_LOG_PATH = STORAGE_PATH / "grid_log.jsonl"


def _ensure_storage_dir() -> None:
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)


def grid_state_path(symbol: str) -> Path:
    """
    Путь к файлу состояния сетки для заданного символа.

    Формат: STORAGE_DIR/<SYMBOL>_grid.json
    """
    symbol = symbol.upper()
    return STORAGE_PATH / f"{symbol}_grid.json"


def load_grid_state(symbol: str) -> Optional[DCAStatePerSymbol]:
    """
    Загрузить состояние сетки для символа, если оно существует.

    Если файла нет или JSON некорректен — возвращает None.
    """
    _ensure_storage_dir()
    path = grid_state_path(symbol)
    if not path.exists():
        return None

    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        data: Dict[str, Any] = json.loads(raw)
    except Exception:
        return None

    try:
        state = DCAStatePerSymbol.from_dict(data)
    except Exception:
        return None
    return state


def save_grid_state(symbol: str, state: DCAStatePerSymbol) -> None:
    """
    Сохранить состояние сетки для символа в <SYMBOL>_grid.json.
    """
    _ensure_storage_dir()
    path = grid_state_path(symbol)
    payload = state.to_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_grid_log(record: Dict[str, Any]) -> None:
    """
    Добавить запись в общий лог сеток grid_log.jsonl.

    Каждая строка — отдельный JSON-объект.
    """
    _ensure_storage_dir()
    rec = dict(record or {})
    rec.setdefault("ts", int(time.time()))
    try:
        line = json.dumps(rec, ensure_ascii=False)
    except Exception:
        # Если запись не сериализуется — пропускаем её.
        return

    with GRID_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
