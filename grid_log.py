import os
import json
import time
from pathlib import Path

"""
Логирование событий, связанных с DCA-сетками.
Формат: JSONL-файл grid_log.jsonl в STORAGE_DIR.
"""

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)
GRID_LOG_PATH = STORAGE_PATH / "grid_log.jsonl"


def _append_event(event: dict) -> None:
    """
    Добавить событие в grid_log.jsonl.
    Ошибки при логировании не должны ломать бота.
    """
    try:
        GRID_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Если директорию не удалось создать — дальше всё равно попробуем писать.
        pass

    try:
        line = json.dumps(event, ensure_ascii=False)
    except Exception:
        # Если объект не сериализуется — логирование пропускаем.
        return

    try:
        with GRID_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        # Логирование — вспомогательная функция, не должна падать наружу.
        return


def log_grid_created(grid: dict) -> None:
    """
    Логирует событие создания сетки (grid_created).

    Ожидается, что grid — это структура, сохранённая в <SYMBOL>_grid.json:
    {
        "symbol": ...,
        "current_grid_id": 1,
        "current_market_mode": ...,
        "current_anchor_price": ...,
        "current_atr_tf1": ...,
        "current_depth_cycle": ...,
        "current_levels": [...],
        "config": {
            "budget_usdc": ...
        },
        ...
    }
    """
    try:
        symbol = grid.get("symbol")
        cfg = grid.get("config") or {}
        levels = grid.get("current_levels") or []
        event = {
            "event": "grid_created",
            "symbol": symbol,
            "ts": int(time.time()),
            "grid_id": grid.get("current_grid_id", 1),
            "market_mode": grid.get("current_market_mode"),
            "anchor_price": grid.get("current_anchor_price"),
            "atr_tf1": grid.get("current_atr_tf1"),
            "depth_cycle": grid.get("current_depth_cycle"),
            "levels": len(levels),
            "budget_usdc": cfg.get("budget_usdc"),
        }
    except Exception:
        return

    _append_event(event)
