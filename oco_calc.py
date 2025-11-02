
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

def _floor(x: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return x
    return (x // step) * step

def _ceil(x: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return x
    if (x % step) == 0:
        return x
    return ((x // step) + 1) * step

def _build_sym(snap: Dict) -> Dict:
    tf = snap.get("tf") or {}
    t12 = tf.get("12h") or {}
    t6  = tf.get("6h") or {}
    return {
        "price": snap.get("price"),
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
    # Only for LONG coins
    if str(snapshot.get("trade_mode") or "SHORT").upper() != "LONG":
        return {}
    tick_str = (snapshot.get("filters") or {}).get("tickSize")
    if not tick_str:
        return {}
    tick = _to_dec(tick_str, "0.01")
    # adaptive band
    r, b, band_low, band_high = adaptive_params_func(_build_sym(snapshot), mode_emoji="📈", oco_range_env=_OCO_RANGE_ENV)
    low = _to_dec(band_low); high = _to_dec(band_high)
    tp = _floor(low, tick)
    trig = _ceil(high, tick)
    lim = trig - tick * _STOP_TICKS  # SL Limit below trigger
    return {
        "TP Limit": float(tp),
        "SL Trigger": float(trig),
        "SL Limit": float(lim),
        "params": {"band_low": float(low), "band_high": float(high), "stop_ticks": _STOP_TICKS}
    }
