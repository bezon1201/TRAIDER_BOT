
from typing import Dict, Any

def market_mode_from_snap(snap: Dict[str, Any]) -> str:
    # Try to use computed field if present
    mode = (snap.get("market", {}) or {}).get("mode_12h")
    if isinstance(mode, str) and mode.upper() in {"UP","DOWN","RANGE"}:
        return mode.upper()
    # Fallback: compare price to bands/ema heuristically
    price = None
    try:
        price = float(snap.get("price", {}).get("last"))
    except Exception:
        price = None
    bands = (snap.get("bands", {}) or {}).get("12h", {})
    low, high = bands.get("low"), bands.get("high")
    try:
        low = float(low) if low is not None else None
        high = float(high) if high is not None else None
    except Exception:
        low, high = None, None
    if price and low and high and low < high:
        if price > high:
            return "UP"
        if price < low:
            return "DOWN"
        return "RANGE"
    # Last resort
    return "RANGE"
