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
    lines = [f"{sym}", f"Price {_i(price)}$ {mtext} {mode}"]

    oco = data.get("oco") or {}
    flags = data.get("flags") or {}
    if isinstance(oco, dict) and all(k in oco for k in ("tp_limit","sl_trigger","sl_limit")):
        pf = flags.get("OCO","")
        prefix = f"{pf}" if pf else ""
        lines.append(f"{prefix}TP {_i(oco['tp_limit'])}$ SLt {_i(oco['sl_trigger'])}$ SL {_i(oco['sl_limit'])}$")

    grid = data.get("grid") or {}
    for k in ("L0","L1","L2","L3"):
        if k in grid and grid[k] is not None:
            pf = (flags or {}).get(k,"")
            prefix = f"{pf}" if pf else ""
            lines.append(f"{prefix}{k} {_i(grid[k])}$")

    return "\n".join(lines)
