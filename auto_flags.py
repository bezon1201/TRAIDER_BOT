
from __future__ import annotations

def _pick(obj: dict, *keys, default=None):
    for k in keys:
        if isinstance(obj, dict) and (k in obj) and obj[k] is not None:
            return obj[k]
    return default

def _norm_mode(mode) -> str:
    m = (str(mode or "")).upper()
    if "UP" in m: return "UP"
    if "DOWN" in m: return "DOWN"
    if "RANGE" in m: return "RANGE"
    return "RANGE"

def _mode_alpha_delta(mode: str) -> tuple[float, float]:
    m = _norm_mode(mode)
    if m == "UP": return (0.7, 0.5)
    if m == "DOWN": return (0.3, 0.2)
    return (0.5, 0.3)

def compute_oco_flag(data: dict) -> str:
    tf12 = (data.get("tf") or {}).get("12h") or {}
    P = float(_pick(data, "price") or _pick(tf12, "close_last") or 0.0)
    TP = float(_pick(data.get("oco") or {}, "tp_limit", default=0.0) or 0.0)
    SLt = float(_pick(data.get("oco") or {}, "sl_trigger", default=0.0) or 0.0)
    MA30 = float(_pick(tf12, "MA30", default=0.0) or 0.0)
    ATR = float(_pick(tf12, "ATR14", default=0.0) or 0.0)
    b = float(_pick(data.get("oco") or {}, "b", default=0.0) or 0.0)
    mm = data.get("market_mode")
    mode = (mm.get("12h") if isinstance(mm, dict) else mm) or "RANGE"
    α, δ = _mode_alpha_delta(mode)
    red_thresh = MA30 + α * ATR + b
    red2 = TP + δ * ATR
    if P > red_thresh and P > red2:
        return "🔴"
    if P <= SLt:
        return "🟢"
    return "🟡"

def compute_L_flag(data: dict, level_key: str) -> str:
    tf12 = (data.get("tf") or {}).get("12h") or {}
    P = float(_pick(data, "price") or _pick(tf12, "close_last") or 0.0)
    grid = data.get("grid") or {}
    L = grid.get(level_key)
    try:
        L = float(L) if L is not None else None
    except Exception:
        L = None
    mm = data.get("market_mode")
    mode = _norm_mode((mm.get("12h") if isinstance(mm, dict) else mm) or "RANGE")
    if level_key == "L2" and mode == "UP":
        return "🔴"
    if level_key == "L3" and mode in ("UP", "RANGE"):
        return "🔴"
    if L is None:
        return "🟡"
    return "🟢" if P <= L else "🟡"

def compute_all_flags(data: dict) -> dict:
    out = {}
    out["OCO"] = compute_oco_flag(data)
    for k in ("L0","L1","L2","L3"):
        out[k] = compute_L_flag(data, k)
    return out
