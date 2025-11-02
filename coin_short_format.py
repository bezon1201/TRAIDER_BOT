
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
    lines = [f"{sym}", f"Price {_i(price)}$ {mtext} Mode {mode}"]
    return "\n".join(lines)
