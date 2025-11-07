from datetime import datetime

from budget import get_pair_budget, get_pair_levels

# Процентное распределение бюджета по режимам рынка (на одну неделю)
WEEKLY_PERCENT = {
    "UP": {
        "OCO": 10,
        "L0": 10,
        "L1": 5,
        "L2": 0,
        "L3": 0,
    },
    "RANGE": {
        "OCO": 5,
        "L0": 5,
        "L1": 10,
        "L2": 5,
        "L3": 0,
    },
    "DOWN": {
        "OCO": 5,
        "L0": 0,
        "L1": 5,
        "L2": 10,
        "L3": 5,
    },
}


def _i(x):
    try:
        return str(int(round(float(x))))
    except Exception:
        return "-"


def build_long_card(data: dict) -> str:
    sym = data.get("symbol", "")
    price = data.get("price") or (data.get("tf") or {}).get("12h", {}).get("close_last")
    market_mode = data.get("market_mode")
    mode = "LONG📈"

    # режим рынка как текст и как ключ UP/RANGE/DOWN
    raw_mode = market_mode.get("12h") if isinstance(market_mode, dict) else market_mode
    raw_mode_str = str(raw_mode or "").upper()
    if "UP" in raw_mode_str:
        mtext = "UP⬆️"
        mode_key = "UP"
    elif "DOWN" in raw_mode_str:
        mtext = "DOWN⬇️"
        mode_key = "DOWN"
    else:
        mtext = "RANGE🔄"
        mode_key = "RANGE"

    # Budget/header lines
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(sym, month)
    budget = int(info.get("budget", 0) or 0)
    reserve = int(info.get("reserve", 0) or 0)
    spent = int(info.get("spent", 0) or 0)
    free = int(info.get("free", budget - reserve - spent) or 0)
    if free < 0:
        free = 0

    week = int(info.get("week", 0) or 0)

    # display month as MM-YYYY
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"
    else:
        mon_disp = month

    header1 = f"{sym} {mon_disp} Wk{week}"
    header2 = f"💰{budget} | ⏳{reserve} | 💸{spent} | 🎯{free}"

    # расчёт сумм по уровням
    perc = WEEKLY_PERCENT.get(mode_key, WEEKLY_PERCENT["RANGE"])
    levels = get_pair_levels(sym, month)

    def _amount_available(level: str) -> int:
        if week <= 0 or budget <= 0:
            return 0
        p = int(perc.get(level, 0) or 0)
        if p <= 0:
            return 0
        quota = int(round(budget * p / 100.0))
        lvl_state = (levels or {}).get(level) or {}
        used = int(lvl_state.get("reserved") or 0) + int(lvl_state.get("spent") or 0)
        avail = quota - used
        if avail < 0:
            avail = 0
        return avail

    def _amt_prefix(level: str, flag: str) -> str:
        """Префикс перед уровнем: либо просто флаг, либо 3-значная сумма + флаг."""
        amt = _amount_available(level)
        if week > 0 and budget > 0:
            # показываем сумму даже если флаг пустой
            return f"{amt:03d}{flag or ''}"
        else:
            return flag or ""

    lines = [header1, header2, f"Price {_i(price)}$ {mtext} {mode}"]

    oco = data.get("oco") or {}
    flags = data.get("flags") or {}
    manual_flags = data.get("flags_manual") or {}

    def _pick_flag(level: str) -> str:
        mf = (manual_flags or {}).get(level)
        if mf:
            return mf
        return (flags or {}).get(level, "")

    if all(k in oco for k in ("tp_limit", "sl_trigger", "sl_limit")):
        pf = _pick_flag("OCO")
        prefix = _amt_prefix("OCO", pf)
        lines.append(f"{prefix}TP {_i(oco['tp_limit'])}$ SLt {_i(oco['sl_trigger'])}$ SL {_i(oco['sl_limit'])}$")

    grid = data.get("grid") or {}
    for k in ("L0", "L1", "L2", "L3"):
        if k in grid and grid[k] is not None:
            pf = _pick_flag(k)
            prefix = _amt_prefix(k, pf)
            lines.append(f"{prefix}{k} {_i(grid[k])}$")

    return "\n".join(lines)
