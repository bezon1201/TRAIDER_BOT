
from typing import Dict, Tuple

def _signal_for_tf(tf_block: Dict) -> str:
    try:
        ma30 = float(tf_block.get("MA30") or 0)
        ma90 = float(tf_block.get("MA90") or 0)
        atr  = float(tf_block.get("ATR14") or 0)
        ma30_arr = list(tf_block.get("MA30_arr") or [])
        ma90_arr = list(tf_block.get("MA90_arr") or [])
    except Exception:
        return "RANGE"
    if atr <= 0:
        return "RANGE"
    d_now = ma30 - ma90
    # d_prev from previous ma values if available
    if len(ma30_arr) >= 2 and len(ma90_arr) >= 2:
        d_prev = float(ma30_arr[-2]) - float(ma90_arr[-2])
    else:
        d_prev = 0.0
    H = 0.4 * atr
    S = 0.1 * atr
    if d_now > +H and (d_now - d_prev) >= +S:
        return "UP"
    if d_now < -H and (d_now - d_prev) <= -S:
        return "DOWN"
    return "RANGE"

def compute_market_mode(tf_dict: Dict, trade_mode: str) -> Tuple[str, Dict[str, str]]:
    """Return overall market_mode ('UP'/'DOWN'/'RANGE') and per-TF signals."""
    signals = {}
    for tf in ("12h","6h","4h","2h"):
        block = tf_dict.get(tf) or {}
        signals[tf] = _signal_for_tf(block)
    md = (trade_mode or "SHORT").upper()
    overall = "RANGE"
    if md == "LONG":
        # Long coins guided by 12h + 6h
        if signals.get("12h") == "UP" and signals.get("6h") == "UP":
            overall = "UP"
        elif signals.get("12h") == "DOWN" or signals.get("6h") == "DOWN":
            overall = "DOWN"
        else:
            overall = "RANGE"
    else:
        # Short coins guided by 4h + 2h
        if signals.get("4h") == "DOWN" or signals.get("2h") == "DOWN":
            overall = "DOWN"
        elif signals.get("4h") == "UP" and signals.get("2h") == "UP":
            overall = "UP"
        else:
            overall = "RANGE"
    return overall, signals
