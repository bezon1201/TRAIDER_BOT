
from typing import Dict, Any

def adaptive_params(snap: Dict[str, Any]) -> Dict[str, float]:
    """Return adaptive band_low/high for 12h window based on snap content.
    Priority:
      1) snap['bands']['buy']['12h']['low'/'high']
      2) snap['bands']['12h']['low'/'high']
      3) derive from simple metrics (ema_12h ± deviation)
    """
    # 1
    bands = snap.get("bands", {})
    buy_bands = bands.get("buy", {}).get("12h", {})
    if {"low", "high"} <= set(buy_bands.keys()):
        return {"band_low": float(buy_bands["low"]), "band_high": float(buy_bands["high"])}
    # 2
    bands12 = bands.get("12h", {})
    if {"low", "high"} <= set(bands12.keys()):
        return {"band_low": float(bands12["low"]), "band_high": float(bands12["high"])}
    # 3 fallback from ema/deviation if present
    metrics = snap.get("metrics", {})
    ema12 = None
    dev = None
    try:
        ema12 = float(metrics.get("ema_12h", {}).get("value"))
    except Exception:
        ema12 = None
    try:
        dev = float(metrics.get("deviation_12h", {}).get("value"))
    except Exception:
        dev = None
    if ema12 and dev:
        return {"band_low": ema12 - 2*dev, "band_high": ema12 - dev}
    if ema12:
        return {"band_low": ema12 * 0.95, "band_high": ema12 * 0.985}
    # Last resort: use last price with a basic discount window
    price = None
    try:
        price = float(snap.get("price", {}).get("last"))
    except Exception:
        price = None
    if price:
        return {"band_low": price * 0.95, "band_high": price * 0.985}
    # give something neutral if absolutely nothing is available
    return {"band_low": 0.0, "band_high": 0.0}
