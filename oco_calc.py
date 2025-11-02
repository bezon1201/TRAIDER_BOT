
from __future__ import annotations
import os
from decimal import Decimal, getcontext
from typing import Dict

getcontext().prec = 28

_OCO_RANGE_ENV = os.getenv("OCO_RANGE", "").strip() or None
_STOP_TICKS = int(os.getenv("STOP_OFFSET_TICKS", "2") or "2")

def _to_dec(x, default="0"):
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal(default)

def _round_floor(val: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return val
    return (val // step) * step

def _round_ceil(val: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return val
    if val % step == 0:
        return val
    return ((val // step) + 1) * step

def _build_sym(snapshot: Dict) -> Dict:
    tf = (snapshot.get("tf") or {})
    t12 = tf.get("12h") or {}
    t6  = tf.get("6h") or {}
    return {
        "price": snapshot.get("price"),
        "MA30_12h": t12.get("MA30"),
        "MA90_12h": t12.get("MA90"),
        "MA30_12h_arr": t12.get("MA30_arr") or [],
        "MA90_12h_arr": t12.get("MA90_arr") or [],
        "ATR14_12h": t12.get("ATR14"),
        "MA30_6h": t6.get("MA30"),
        "MA90_6h": t6.get("MA90"),
        "MA30_6h_arr": t6.get("MA30_arr") or [],
        "MA90_6h_arr": t6.get("MA90_arr") or [],
        "ATR14_6h": t6.get("ATR14"),
    }

def compute_oco_buy(snapshot: Dict, adaptive_params_func) -> Dict:
    trade_mode = str(snapshot.get("trade_mode") or "SHORT").upper()
    if trade_mode != "LONG":
        return {}

    tick_str = (snapshot.get("filters") or {}).get("tickSize")
    if not tick_str:
        return {}
    tick = _to_dec(tick_str, "0.01")

    sym = _build_sym(snapshot)
    r, b, band_low, band_high = adaptive_params_func(sym, mode_emoji="📈", oco_range_env=_OCO_RANGE_ENV)

    low = _to_dec(band_low)
    high = _to_dec(band_high)

    tp_limit = _round_floor(low, tick)
    sl_trigger = _round_ceil(high, tick)
    sl_limit = sl_trigger - tick * _STOP_TICKS

    return {
        "TP Limit": float(tp_limit),
        "SL Trigger": float(sl_trigger),
        "SL Limit": float(sl_limit),
        "params": {"band_low": float(low), "band_high": float(high), "stop_ticks": _STOP_TICKS}
    }
