
from __future__ import annotations

from datetime import datetime
from budget import get_pair_levels, get_pair_budget

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



def _budget_flag_for_level(data: dict, level_key: str) -> str | None:
    """Дополнительные флаги на основе бюджета.

    Логика приоритета:
    - ✅ если по уровню был FILL в ТЕКУЩУЮ неделю;
    - ⚠️ если по уровню есть открытый виртуальный ордер (reserved > 0);
    - иначе — нет «бюджетного» флага, используется автофлаг 🔴/🟡/🟢.
    """
    symbol = (data.get("symbol") or "").upper().strip()
    if not symbol:
        return None

    month = datetime.now().strftime("%Y-%m")
    try:
        levels = get_pair_levels(symbol, month)
        info = get_pair_budget(symbol, month)
    except Exception:
        return None

    st = (levels.get(level_key) or {}) if isinstance(levels, dict) else {}
    try:
        reserved = int(st.get("reserved") or 0)
    except Exception:
        reserved = 0
    try:
        last_fill_week = int(st.get("last_fill_week") if st.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1

    try:
        current_week = int(info.get("week") or 0)
    except Exception:
        current_week = 0

    # ✅ — если ордер по уровню исполнялся в текущую неделю
    if current_week > 0 and last_fill_week == current_week:
        return "✅"
    # ⚠️ — если по уровню есть открытый виртуальный ордер
    if reserved > 0:
        return "⚠️"
    return None


def compute_oco_flag(data: dict) -> str:
    # Сначала смотрим на состояние бюджета: ✅/⚠️ имеют приоритет над автофлагами.
    manual = _budget_flag_for_level(data, "OCO")
    if manual:
        return manual

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
    # Приоритет бюджетных флагов: ✅ (spent>0), ⚠️ (reserved>0).
    manual = _budget_flag_for_level(data, level_key)
    if manual:
        return manual

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
    if level_key == "L0" and mode == "DOWN":
        return "🔴"
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
