from datetime import datetime

from budget import get_pair_budget


def _i(x):
    try:
        return str(int(round(float(x))))
    except Exception:
        return "-"


def build_short_card(data: dict) -> str:
    sym = data.get("symbol", "")
    price = data.get("price") or (data.get("tf") or {}).get("12h", {}).get("close_last")
    market_mode = data.get("market_mode")
    mode = "SHORT📉"
    mtext = market_mode.get("12h") if isinstance(market_mode, dict) else market_mode
    mtext = str(mtext or "").upper()
    if "UP" in mtext:
        mtext = "UP⬆️"
    elif "DOWN" in mtext:
        mtext = "DOWN⬇️"
    else:
        mtext = "RANGE🔄"

    # Budget/header line (same as for LONG)
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
    return "\n".join(lines)
