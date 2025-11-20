from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict

from config import STORAGE_DIR, TF1, TF2
from dca_config import get_symbol_config
from dca_models import DCAConfigPerSymbol, DCAStatePerSymbol

log = logging.getLogger(__name__)

STORAGE_PATH = Path(STORAGE_DIR)

# Глубина цикла в ATR для разных режимов рынка.
# Значения взяты из OLD BOT и оставлены константами.
GRID_DEPTH_UP = 2
GRID_DEPTH_RANGE = 3
GRID_DEPTH_DOWN = 6


def _state_path(symbol: str) -> Path:
    """Путь к файлу <SYMBOL>state.json с агрегированным состоянием монеты."""
    symbol = (symbol or "").upper()
    return STORAGE_PATH / f"{symbol}state.json"


def _grid_path(symbol: str) -> Path:
    """Путь к файлу <SYMBOL>_grid.json с описанием DCA-сетки."""
    symbol = (symbol or "").upper()
    return STORAGE_PATH / f"{symbol}_grid.json"


def _depth_multiplier_for_mode(market_mode: str) -> int:
    """Коэффициент глубины сетки в зависимости от рыночного режима."""
    mode = (market_mode or "RANGE").upper()
    if mode == "UP":
        return GRID_DEPTH_UP
    if mode == "DOWN":
        return GRID_DEPTH_DOWN
    return GRID_DEPTH_RANGE


def _load_state_for_symbol(symbol: str) -> Dict[str, Any]:
    """Загружает <SYMBOL>state.json. При ошибке возвращает пустой dict."""
    path = _state_path(symbol)
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        log.warning("Не удалось прочитать state для %s из %s", symbol, path)
        return {}


def _build_grid_for_symbol(
    symbol: str,
    cfg: DCAConfigPerSymbol,
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """Строит структуру <SYMBOL>_grid.json на основе state и DCA-конфига.

    Логика целиком повторяет OLD BOT, за исключением выбора anchor:
    вместо автоматического _select_anchor_price(...) используется cfg.anchor_price,
    который пользователь задаёт через кнопку ANCHOR.
    """
    symbol_u = (symbol or "").upper()
    now_ts = int(time.time())

    tf1 = str(state.get("tf1") or TF1)
    tf2 = str(state.get("tf2") or TF2)
    market_mode = str(state.get("market_mode") or "RANGE").upper()

    # ATR(TF1)
    try:
        atr = float(state.get("ATR14") or 0.0)
    except Exception:  # noqa: BLE001
        atr = 0.0

    # Якорная цена сетки — теперь исключительно из конфига
    try:
        anchor_price = float(getattr(cfg, "anchor_price", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        anchor_price = 0.0

    if atr <= 0 or anchor_price <= 0:
        raise ValueError("ATR или anchor_price не заданы или некорректны.")

    depth_mult = _depth_multiplier_for_mode(market_mode)
    depth = float(depth_mult) * atr

    levels = int(getattr(cfg, "levels_count", 0) or 0)
    budget = float(getattr(cfg, "budget_usdc", 0.0) or 0.0)

    if levels <= 0 or budget <= 0:
        raise ValueError("Неверные параметры конфига DCA (budget или levels).")

    if levels == 1:
        step = 0.0
    else:
        step = depth / (levels - 1) if depth > 0 else 0.0

    notional_per_level = budget / levels

    current_levels = []
    for idx in range(levels):
        price = anchor_price - idx * step
        if price <= 0:
            price = anchor_price
        qty = notional_per_level / price if price > 0 else 0.0
        current_levels.append(
            {
                "level_index": idx + 1,
                "grid_id": 1,
                "price": round(price, 8),
                "qty": round(qty, 8),
                "notional": round(notional_per_level, 2),
                "filled": False,
                "filled_ts": None,
            }
        )

    # В новом боте в DCAConfigPerSymbol остался только updated_ts.
    # Для created_ts используем updated_ts если он есть, иначе now_ts.
    updated_ts_cfg = getattr(cfg, "updated_ts", None)
    created_ts = int(updated_ts_cfg or now_ts)
    updated_ts = int(updated_ts_cfg or now_ts)

    grid: Dict[str, Any] = {
        "symbol": symbol_u,
        "tf1": tf1,
        "tf2": tf2,
        "campaign_start_ts": now_ts,
        "campaign_end_ts": None,
        "config": {
            "symbol": cfg.symbol,
            "enabled": bool(getattr(cfg, "enabled", False)),
            "budget_usdc": budget,
            "levels_count": levels,
            "base_tf": getattr(cfg, "base_tf", None),
            "created_ts": created_ts,
            "updated_ts": updated_ts,
        },
        "total_levels": levels,
        "filled_levels": 0,
        "remaining_levels": levels,
        "spent_usdc": 0.0,
        "avg_price": None,
        "current_grid_id": 1,
        "current_market_mode": market_mode,
        "current_anchor_price": anchor_price,
        "current_atr_tf1": atr,
        "current_depth_cycle": depth,
        "current_levels": current_levels,
        "created_ts": now_ts,
        "updated_ts": now_ts,
    }
    return grid


def build_and_save_dca_grid(symbol: str) -> DCAStatePerSymbol:
    """Строит и сохраняет DCA-сетку для symbol.

    1. Читает DCA-конфиг для пары.
    2. Читает state из <SYMBOL>state.json.
    3. Строит структуру сетки (как в OLD BOT, но с anchor из конфига).
    4. Сохраняет <SYMBOL>_grid.json.
    5. Возвращает DCAStatePerSymbol для удобного дальнейшего использования.

    При проблемах выбрасывает ValueError с текстом ошибки.
    """
    symbol_u = (symbol or "").upper()
    if not symbol_u:
        raise ValueError("Пустой symbol при построении DCA-сетки.")

    cfg = get_symbol_config(symbol_u)
    if cfg is None:
        raise ValueError(f"DCA: конфиг для {symbol_u} не найден.")

    state = _load_state_for_symbol(symbol_u)
    if not state:
        raise ValueError(
            f"DCA: state для {symbol_u} не найден. Сначала выполните METRICS/ROLLOVER."
        )

    grid_dict = _build_grid_for_symbol(symbol_u, cfg, state)

    gpath = _grid_path(symbol_u)
    try:
        gpath.parent.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        # Папку создадим позже или она уже существует — не критично.
        pass

    try:
        with gpath.open("w", encoding="utf-8") as f:
            json.dump(grid_dict, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        log.exception("Не удалось сохранить файл сетки для %s: %s", symbol_u, e)
        raise ValueError(f"DCA: не удалось сохранить файл сетки для {symbol_u}: {e}") from e

    # Пока логирование в jsonl (grid_log) не переносим — сделаем отдельным шагом.
    # Возвращаем нормализованное состояние сетки.
    return DCAStatePerSymbol.from_dict(grid_dict)
