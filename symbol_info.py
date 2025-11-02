
import os, json

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

def _coin_path(symbol: str) -> str:
    return os.path.join(STORAGE_DIR, f"{symbol}.json")

def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _i(x):
    try:
        return str(int(round(float(x))))
    except Exception:
        return "-"

def build_symbol_message(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    d = _read_json(_coin_path(sym))
    price = (d.get("price") or (d.get("tf") or {}).get("12h", {}).get("close_last"))
    trade_mode = (d.get("trade_mode") or "").upper()

    lines = [sym, f"Price {_i(price)}$"]

    if trade_mode == "LONG":
        lines.append("OCO Buy")
        oco = d.get("oco") or {}
        flags = d.get("flags") or {}
        if all(k in oco for k in ("tp_limit","sl_trigger","sl_limit")):
            pf = flags.get("OCO","")
            prefix = f"{pf}" if pf else ""
            lines.append(f"{prefix}TP {_i(oco['tp_limit'])}$ SLt {_i(oco['sl_trigger'])}$ SL {_i(oco['sl_limit'])}$")
        grid = d.get("grid") or {}
        for k in ("L0","L1","L2","L3"):
            if k in grid and grid[k] is not None:
                pf = (flags or {}).get(k,"")
                prefix = f"{pf}" if pf else ""
                lines.append(f"{prefix}{k} {_i(grid[k])}$")
    else:
        lines.append("Mode SHORT")

    return "\n".join(lines)
