
from __future__ import annotations
import math

def _clip(x, lo, hi):
    return max(lo, min(hi, x))

def _to_f(x):
    try:
        return float(x)
    except Exception:
        return None

def _floor_tick(v, tick):
    if tick and tick > 0:
        return math.floor(v / tick) * tick
    return v

def compute_grid_levels(sym: dict) -> dict:
    """Сетка лимитов L1..L3 на базе M=MA30_12h и A=ATR14_12h.
    Дополнительно L0 = середина между tp_limit и L1 (округляется вниз по тикам).
    Логика перенесена из старого бота «как есть».
    """
    tf = (sym.get("tf") or {}).get("12h") or {}
    M = _to_f(tf.get("MA30"))
    A = _to_f(tf.get("ATR14"))
    if not M or not A or A <= 0:
        return {}
    price = _to_f(sym.get("price")) or _to_f(tf.get("close_last"))
    # tick
    tick_s = str(((sym.get("filters") or {}).get("tickSize")) or "0.01")
    try:
        tick = float(tick_s)
    except Exception:
        tick = 0.01
    # market mode (берем 12h)
    mm12 = (sym.get("market_mode") or {}).get("12h") or ""
    mlow = str(mm12).lower()
    is_up = ("up" in mlow) or ("⬆" in str(mm12))
    is_down = ("down" in mlow) or ("⬇" in str(mm12))
    # дистанция от M до цены (в долях ATR)
    D = 0.0
    if price is not None and A and A > 0:
        try:
            D = _clip((M - price) / (0.8 * A), 0.0, 1.0)
        except Exception:
            D = 0.0
    # Базовый K1 по режиму
    K1_base = 1.4
    if is_up:
        K1_base = 1.1
    elif is_down:
        K1_base = 1.8
    # вес тренда
    trend_weight = 0.7 if (is_up or is_down) else 0.3
    # итоговые множители (как в старом коде)
    K1  = _clip(K1_base + (0.30 if is_down else 0.0) - (0.20 * (0.7 if is_up else 0.3)) + 0.40 * D, 0.6, 2.4)
    D12 = _clip(0.6 + 0.40 * D + 0.20 * trend_weight, 0.3, 1.2)
    D23 = _clip(0.9 + 0.50 * D + 0.30 * trend_weight, 0.4, 1.6)
    # уровни
    L1 = _floor_tick(M - K1 * A, tick)
    L2 = _floor_tick(M - (K1 + D12) * A, tick)
    L3 = _floor_tick(M - (K1 + D12 + D23) * A, tick)
    # L0 — середина между tp_limit и L1
    oco = sym.get("oco") or {}
    tp = _to_f(oco.get("tp_limit"))
    L0 = None
    if tp is not None and L1 is not None:
        L0 = _floor_tick((tp + L1) / 2.0, tick)
    out = {"L1": L1, "L2": L2, "L3": L3}
    if L0 is not None:
        out["L0"] = L0
    return out
