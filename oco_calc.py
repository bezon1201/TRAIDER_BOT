
from typing import Dict, Any
from .oco_params import adaptive_params
from .utils import normalize_tick

def _tick_size_from_snap(snap: Dict[str, Any]) -> float:
    tick = None
    try:
        tick = float(snap.get("symbol", {}).get("tickSize"))
    except Exception:
        tick = None
    if not tick or tick <= 0:
        # fallback per symbol convention
        sym = str(snap.get("symbol", {}).get("name", "")).upper()
        if "BTC" in sym:
            return 0.01
        if "ETH" in sym:
            return 0.01
        return 0.001
    return tick

def compute_oco_buy(snap: Dict[str, Any], params_fn=adaptive_params) -> Dict[str, float]:
    """Compute OCO buy (LONG only) for 12h band.

    TP Limit  = floor(band_low, tick)

    SL Trigger= ceil(band_high, tick)

    SL Limit  = SL Trigger - 2*tick

    Returns empty dict if snap.trade_mode != LONG.
    """
    trade_mode = str(snap.get("trade_mode", "SHORT")).upper()
    if trade_mode != "LONG":
        return {}
    p = params_fn(snap)
    band_low, band_high = p.get("band_low", 0.0), p.get("band_high", 0.0)
    tick = _tick_size_from_snap(snap)
    if band_low <= 0 or band_high <= 0 or band_low >= band_high:
        return {}
    tp = normalize_tick(band_low, tick, "floor")
    st = normalize_tick(band_high, tick, "ceil")
    sl = round(st - 2*tick, 8)
    return {"TP Limit": tp, "SL Trigger": st, "SL Limit": sl, "tickSize": tick}
