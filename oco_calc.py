
from __future__ import annotations
import math
from typing import Dict, Any, Optional

def _to_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def _tick_round_down(value: float, tick: float) -> float:
    if not tick or tick <= 0:
        return value
    return math.floor(value / tick) * tick

def _tick_round_up(value: float, tick: float) -> float:
    if not tick or tick <= 0:
        return value
    return math.ceil(value / tick) * tick

def _clip(v, lo, hi):
    return max(lo, min(hi, v))

def compute_oco_sell(data: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """
    Compute SELL OCO (take-profit limit, stop trigger, stop limit) for LONG strategy.
    Uses 12h MA/ATR as basis with trend-aware shift similar to legacy oco_params.py.

    Expects structure like metrics_runner output:
      data = { "trade_mode": "LONG", "filters": {"tickSize": "..."},
               "tf": {"12h":{"MA30":..,"MA90":..,"ATR14":..}, "6h":{...}}, ... }
    Returns dict with tp_limit, sl_trigger, sl_limit and diagnostics (b, r, basis).
    """
    if (data.get("trade_mode") or "").upper() != "LONG":
        return None
    tf = data.get("tf") or {}
    tf12 = tf.get("12h") or {}
    tf6  = tf.get("6h") or {}
    M  = _to_float(tf12.get("MA30"))
    M90 = _to_float(tf12.get("MA90"))
    ATR12 = _to_float(tf12.get("ATR14"))
    ATR6  = _to_float((tf6 or {}).get("ATR14"))
    price = _to_float((tf12 or {}).get("close_last")) or _to_float(data.get("price"))
    if M is None or M90 is None or ATR12 is None or not ATR12 or price is None:
        return None

    # Trend score T from distance and slope proxies (bounded 0..1)
    d1 = (M - M90)
    # slope proxy from last 5 of MA30
    ma_arr = tf12.get("MA30_arr") or []
    slope = 0.0
    if isinstance(ma_arr, list) and len(ma_arr) >= 5:
        slope = (ma_arr[-1] - ma_arr[-5]) / 4.0
    # normalize
    T_dist = _clip(abs(d1) / (2.0 * ATR12), 0.0, 1.0)
    T_slope = _clip(abs(slope) / (2.0 * ATR12), 0.0, 1.0)
    T = 0.6 * T_dist + 0.4 * T_slope

    # near MA?
    near = abs(price - M) <= 0.2 * ATR12

    # offset b and width r
    b = 0.0 if near else (0.20 + 0.35 * T)  # 0..0.55
    atrp = ATR12 / M if M else 0.0
    # base r from vol 2%..8% => 0.35..0.75
    if atrp <= 0.02:
        r_vol = 0.35
    elif atrp >= 0.08:
        r_vol = 0.75
    else:
        r_vol = 0.35 + (atrp - 0.02) * (0.75 - 0.35) / (0.06)
    r = r_vol * (1 - 0.2 * T)
    if ATR6 and ATR12:
        ratio = _clip(ATR6 / ATR12, 0.9, 1.1)
        r *= ratio
    r = _clip(r, 0.30, 0.90)

    half = r * ATR12

    # For LONG we want SELL OCO:
    # Take profit around upper band; stop under lower band.
    high = M + (1 - b) * half   # bullish side
    low  = M - (1 + b) * half   # bearish side

    # rounding by tick
    tick_str = ((data.get("filters") or {}).get("tickSize") or "0.01")
    try:
        tick = float(tick_str)
    except Exception:
        tick = 0.01

    tp_limit = _tick_round_down(high, tick)
    sl_trigger = _tick_round_down(low, tick)
    # place SL limit slightly under trigger (0.05% or 3 ticks, whichever larger)
    sl_gap = max(3 * tick, 0.0005 * price)
    sl_limit = _tick_round_down(max(0.0, sl_trigger - sl_gap), tick)

    return {
        "tp_limit": round(tp_limit, 8),
        "sl_trigger": round(sl_trigger, 8),
        "sl_limit": round(sl_limit, 8),
        "basis": round(M, 8),
        "atr": round(ATR12, 8),
        "r": round(r, 6),
        "b": round(b, 6),
    }



def compute_oco_buy(sdata: dict) -> dict:
    """BUY-OCO prices: tp_limit (below), sl_trigger (above), sl_limit = sl_trigger + 2*tick"""
    filters = sdata.get("filters", {}) if isinstance(sdata, dict) else {}
    try:
        tick = float(filters.get("tickSize") or 0.0)
    except Exception:
        tick = 0.0
    if not tick:
        tick = 0.01
    try:
        last = float(sdata.get("last") or 0.0)
    except Exception:
        last = 0.0
    base = {}
    try:
        base = compute_oco_sell(sdata) or {}
    except Exception:
        base = {}
    def _f(x, d=0.0):
        try: return float(x)
        except Exception: return d
    a = _f(base.get("tp_limit"), 0.0)
    b = _f(base.get("sl_trigger"), 0.0)
    c = _f(base.get("sl_limit"), 0.0)
    lo = min(a,b,c)
    hi = max(a,b,c)
    sl_trigger = hi
    if last and sl_trigger <= last:
        sl_trigger = last + tick
    k = 2
    sl_limit = sl_trigger + k*tick
    def round_up(x, step):
        if step <= 0: return x
        n = int((x + 1e-15)/step + 0.999999)
        return n*step
    def round_dn(x, step):
        if step <= 0: return x
        n = int((x + 1e-15)/step)
        return n*step
    tp_limit = round_dn(lo, tick)
    sl_trigger = round_up(sl_trigger, tick)
    sl_limit = round_up(sl_limit, tick)
    return {"tp_limit": tp_limit, "sl_trigger": sl_trigger, "sl_limit": sl_limit}
