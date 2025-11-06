from datetime import datetime

from budget import get_pair_budget


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
    mtext = market_mode.get("12h") if isinstance(market_mode, dict) else market_mode
    mtext = str(mtext or "").upper()
    if "UP" in mtext:
        mtext = "UP⬆️"
    elif "DOWN" in mtext:
        mtext = "DOWN⬇️"
    else:
        mtext = "RANGE🔄"

    # Budget/header line
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(sym, month)
    budget = int(info.get("budget", 0) or 0)
    reserve = int(info.get("reserve", 0) or 0)
    spent = int(info.get("spent", 0) or 0)
    free = int(info.get("free", budget - reserve - spent) or 0)
    if free < 0:
        free = 0
    header = f"{sym} 💰{budget} | ⏳{reserve} | 💸{spent} | 🎯{free}"

    lines = [header, f"Price {_i(price)}$ {mtext} {mode}"]

    oco = data.get("oco") or {}
    flags = data.get("flags") or {}
    if all(k in oco for k in ("tp_limit", "sl_trigger", "sl_limit")):
        pf = flags.get("OCO", "")
        prefix = f"{pf}" if pf else ""
        lines.append(f"{prefix}TP {_i(oco['tp_limit'])}$ SLt {_i(oco['sl_trigger'])}$ SL {_i(oco['sl_limit'])}$")

    grid = data.get("grid") or {}
    for k in ("L0", "L1", "L2", "L3"):
        if k in grid and grid[k] is not None:
            pf = (flags or {}).get(k, "")
            prefix = f"{pf}" if pf else ""
            lines.append(f"{prefix}{k} {_i(grid[k])}$")

    return "\n".join(lines)
