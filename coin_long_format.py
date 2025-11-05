def _i(x):
    try:
        return str(int(round(float(x))))
    except Exception:
        return "-"

def _ia(x):
    try:
        v = float(x)
        return str(int(round(v)))
    except Exception:
        return "0"

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

    lines = [f"{sym}", f"Price {_i(price)}$ {mtext} {mode}"]
    oco = data.get("oco") or {}
    flags = data.get("flags") or {}
    pockets = (data.get("pockets") or {})
    alloc_amt = (pockets.get("alloc_amt") or {})
    # compute padding width based on all pocket amounts
    _vals = [int(float(alloc_amt.get(k, 0) or 0)) for k in ("OCO","L0","L1","L2","L3")]
    _maxd = max(len(str(v)) for v in _vals) if _vals else 1
    def _pad(n:int)->str:
        d = len(str(n))
        return " " * (2 * (_maxd - d)) + str(n)
    if all(k in oco for k in ("tp_limit","sl_trigger","sl_limit")):
        pf = flags.get("OCO","")
        amt = int(float(alloc_amt.get("OCO", 0) or 0))
        prefix = f"{_pad(amt)}{pf}" if pf else f"{_pad(amt)}"
        lines.append(f"{prefix}TP {_i(oco['tp_limit'])}$ SLt {_i(oco['sl_trigger'])}$ SL {_i(oco['sl_limit'])}$")

    grid = data.get("grid") or {}
    for k in ("L0","L1","L2","L3"):
        if k in grid and grid[k] is not None:
            pf = (flags or {}).get(k,"")
            amt = int(float(alloc_amt.get(k, 0) or 0))
            prefix = f"{_pad(amt)}{pf}" if pf else f"{_pad(amt)}"
            lines.append(f"{prefix}{k} {_i(grid[k])}$")

    return "\n".join(lines)
