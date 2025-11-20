from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from config import STORAGE_DIR


def get_min_notional_from_state(state: Dict[str, Any]) -> float:
    """Извлечь minNotional из структуры state (как в <SYMBOL>state.json>).

    Приоритет источников:
    1) trading_params.filters["NOTIONAL"].minNotional_f
    2) trading_params.filters["NOTIONAL"].minNotional
    3) trading_params.symbol_info.min_notional
    """
    if not isinstance(state, dict):
        raise TypeError("state must be a dict")

    tp = state.get("trading_params") or {}
    if not isinstance(tp, dict):
        tp = {}

    filters = tp.get("filters") or {}
    if not isinstance(filters, dict):
        filters = {}

    notional_filter = filters.get("NOTIONAL") or {}
    if not isinstance(notional_filter, dict):
        notional_filter = {}

    value = notional_filter.get("minNotional_f")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass

    value = notional_filter.get("minNotional")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass

    symbol_info = tp.get("symbol_info") or {}
    if isinstance(symbol_info, dict):
        value = symbol_info.get("min_notional")
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass

    raise ValueError("minNotional not found in state")


def get_symbol_min_notional(symbol: str) -> float:
    """Загрузить <SYMBOL>state.json из STORAGE_DIR и вернуть minNotional."""
    symbol = symbol.upper()
    path = Path(STORAGE_DIR) / f"{symbol}state.json"

    if not path.exists():
        raise FileNotFoundError(f"State file not found for symbol {symbol}: {path}")

    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)

    return get_min_notional_from_state(state)
