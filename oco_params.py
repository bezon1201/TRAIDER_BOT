def _clip(x, lo, hi): return max(lo, min(hi, x))
def _to_float(x):
    try: return float(x)
    except Exception: return None

def _deltas_pair(m30_arr, m90_arr):
    try:
        if not (m30_arr and m90_arr): return None
        if len(m30_arr) == 1 or len(m90_arr) == 1:
            d1 = float(m30_arr[-1]) - float(m90_arr[-1]); d2 = d1
        else:
            d1 = float(m30_arr[-1]) - float(m90_arr[-1])
            d2 = float(m30_arr[-2]) - float(m90_arr[-2])
        return d1, d2
    except Exception: return None

def _round_floor(v, tick):
    if tick and tick > 0: return int(v / tick) * tick
    return v

def _round_ceil(v, tick):
    if tick and tick > 0:
        q = int(v / tick)
        return v if q * tick == v else (q + 1) * tick
    return v

def _trend_strength(sym: dict) -> float:
    m30_12 = sym.get("MA30_12h_arr") or []
    m90_12 = sym.get("MA90_12h_arr") or []
    m30_6  = sym.get("MA30_6h_arr") or []
    m90_6  = sym.get("MA90_6h_arr") or []
    atr12  = sym.get("ATR14_12h") or 0.0
    atr6   = sym.get("ATR14_6h")  or 0.0

    dd12 = _deltas_pair(m30_12, m90_12)
    if not dd12 or not atr12: return 0.0
    d1, d2 = dd12
    slope = d1 - d2
    sign = (1.0 if d1 > 0 else (-1.0 if d1 < 0 else 0.0))

    T_dist  = _clip(abs(d1) / (0.9 * atr12), 0.0, 1.0)
    T_slope = _clip((sign * slope) / (0.2 * atr12), 0.0, 1.0) if sign != 0 else 0.0
    T = 0.6 * T_dist + 0.4 * T_slope

    # 6h согласованность
    dd6 = _deltas_pair(m30_6, m90_6)
    if dd6 and atr6:
        d1x, d2x = dd6
        slopex = d1x - d2x
        H = 0.4 * atr6
        S = 0.1 * atr6
        tf6 = "UP" if (d1x > +H and slopex >= +S) else ("DOWN" if (d1x < -H and slopex <= -S) else "RANGE")
        if   sign > 0 and tf6 == "UP":   T += 0.2
        elif sign < 0 and tf6 == "DOWN": T += 0.2
        elif sign != 0 and ((sign > 0 and tf6 == "DOWN") or (sign < 0 and tf6 == "UP")): T -= 0.2

    return _clip(T, 0.0, 1.0)

_STATE = {"r": {}, "b": {}}

def adaptive_params(sym: dict, mode_emoji: str, oco_range_env: str | None):

    price = sym.get("price")
    M = _to_float(sym.get("MA30_12h"))
    atr12 = _to_float(sym.get("ATR14_12h")) or 0.0
    atr6  = _to_float(sym.get("ATR14_6h"))  or 0.0
    m30_12a = sym.get("MA30_12h_arr") or []
    m90_12a = sym.get("MA90_12h_arr") or []
    if not (M and atr12): return 0.5, 0.0, M or 0.0, M or 0.0

    T = _trend_strength(sym)
    dd12 = _deltas_pair(m30_12a, m90_12a) or (0.0, 0.0)
    d1 = dd12[0]
    trend_sign = (1 if d1 > 0 else (-1 if d1 < 0 else 0))

    # "Около МА30"
    near = (abs(dd12[0]) < 0.2 * atr12) and (price is not None) and (abs(price - M) < 0.2 * atr12)

    # Смещение
    if near:
        b = 0.0
    else:
        b = _clip(0.20 + (0.55 - 0.20) * T, 0.20, 0.55)

    # Ширина r
    r_env = None
    if oco_range_env is not None and str(oco_range_env).strip().upper() != "FALSE" and str(oco_range_env).strip() != "":
        try: r_env = float(oco_range_env)
        except Exception: r_env = None

    if r_env and r_env > 0:
        r = r_env
    else:
        atrp = (atr12 / M) if M else 0.0
        def lin(x, x1, y1, x2, y2):
            return y1 if x2 == x1 else y1 + ((x - x1) / (x2 - x1)) * (y2 - y1)
        r_vol = _clip(lin(atrp, 0.02, 0.35, 0.08, 0.75), 0.35, 0.75)
        r = r_vol if near else r_vol * (1.0 - 0.2 * T)
        ratio = _clip((atr6 / atr12) if atr12 > 0 else 1.0, 0.9, 1.1)
        r = _clip(r * ratio, 0.30, 0.90)

    # Сглаживание
    sym_name = sym.get("_symbol") or "SYM"
    prev_r = _STATE["r"].get(sym_name)
    prev_b = _STATE["b"].get(sym_name)
    r_eff = r if prev_r is None else (prev_r if abs(r - prev_r) < 0.05 else (prev_r * 0.7 + r * 0.3))
    b_eff = b if prev_b is None else (prev_b if abs(b - prev_b) < 0.05 else (prev_b * 0.7 + b * 0.3))
    _STATE["r"][sym_name] = r_eff
    _STATE["b"][sym_name] = b_eff

    # Коридор
    half = r_eff * atr12
    M = float(M)
    if trend_sign > 0:
        low  = M - (1 - b_eff) * half
        high = M + (1 + b_eff) * half
    elif trend_sign < 0:
        low  = M - (1 + b_eff) * half
        high = M + (1 - b_eff) * half
    else:
        low, high = M - half, M + half

    return r_eff, b_eff, low, high

def order_prices(sym: dict, stop_offset_ticks: int = 2) -> dict:
    band_low  = _to_float(sym.get("band_low"))
    band_high = _to_float(sym.get("band_high"))
    tick = _to_float(sym.get("tickSize"))
    if band_low is None or band_high is None or tick is None or tick <= 0: return {}
    tp = _round_floor(band_low, tick)
    st = _round_ceil(band_high, tick)
    return {"tp_limit": tp, "sl_trigger": st}
