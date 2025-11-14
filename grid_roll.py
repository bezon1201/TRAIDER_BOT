import os
import json
import time
from pathlib import Path
from typing import Optional

from dca_config import get_symbol_config
from dca_models import DCAConfigPerSymbol
from dca_handlers import _load_state_for_symbol, _build_grid_for_symbol
from grid_log import log_grid_rolled

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)


def _grid_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}_grid.json"


def roll_grid_for_symbol(symbol: str) -> bool:
    """
    Перестраивает DCA-сетку для символа на основе актуального state.

    Вызывается:
    - при /market force (ручной пересчёт market/state),
    - при publish планировщика (step2 market force).
    """
    symbol = (symbol or "").upper()
    if not symbol:
        return False

    gpath = _grid_path(symbol)
    if not gpath.exists():
        # Нет активной кампании/сетки — ничего не делаем.
        return False

    try:
        raw = gpath.read_text(encoding="utf-8")
        grid = json.loads(raw)
    except Exception:
        return False

    # Кампания уже завершена?
    if grid.get("campaign_end_ts"):
        return False

    try:
        old_grid_id = int(grid.get("current_grid_id", 1) or 1)
    except Exception:
        old_grid_id = 1

    cfg: Optional[DCAConfigPerSymbol] = get_symbol_config(symbol)
    if cfg is None:
        # Нет DCA-конфига — не трогаем сетку.
        return False

    state = _load_state_for_symbol(symbol)
    if not state:
        # Нет актуального state — не можем пересчитать сетку.
        return False

    try:
        new_grid = _build_grid_for_symbol(symbol, cfg, state)
    except Exception:
        # Если не удалось построить новую сетку — не трогаем старую.
        return False

    # Переносим "жизнь кампании" и агрегированные поля.
    now_ts = int(time.time())
    new_grid["campaign_start_ts"] = grid.get("campaign_start_ts", new_grid.get("campaign_start_ts", now_ts))
    new_grid["campaign_end_ts"] = grid.get("campaign_end_ts")
    new_grid["current_grid_id"] = old_grid_id + 1

    new_grid["total_levels"] = grid.get("total_levels", new_grid.get("total_levels"))
    new_grid["filled_levels"] = grid.get("filled_levels", 0)
    new_grid["spent_usdc"] = grid.get("spent_usdc", 0.0)

    new_grid["created_ts"] = grid.get("created_ts", new_grid.get("created_ts", now_ts))
    new_grid["updated_ts"] = now_ts

    # remaining_levels как задел на будущее: total_levels - filled_levels
    try:
        total_levels = int(new_grid.get("total_levels", 0) or 0)
        filled_levels = int(new_grid.get("filled_levels", 0) or 0)
        remaining_levels = max(total_levels - filled_levels, 0)
    except Exception:
        remaining_levels = new_grid.get("remaining_levels")
    new_grid["remaining_levels"] = remaining_levels

    # Сохраняем обновлённую сетку.
    try:
        with gpath.open("w", encoding="utf-8") as f:
            json.dump(new_grid, f, ensure_ascii=False, indent=2)
    except Exception:
        return False

    # Логируем roll.
    try:
        log_grid_rolled(new_grid, from_grid_id=old_grid_id)
    except Exception:
        # Логирование не должно ломать основную логику.
        pass

    return True
