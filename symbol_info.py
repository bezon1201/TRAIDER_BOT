
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

def _fmt_int(v) -> str:
    try:
        return str(int(round(float(v))))
    except Exception:
        return "-"

def build_symbol_message(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    if not sym:
        return "Некорректный символ"
    data = _read_json(_coin_path(sym))
    if not data:
        return f"{sym}\nНет данных"

    price = data.get("price") or (data.get("tf") or {}).get("12h", {}).get("close_last")
    trade_mode = (data.get("trade_mode") or "").upper()

    lines = [sym, f"Price {_fmt_int(price)}$"]

    if trade_mode == "LONG":
        lines.append("OCO Buy")
        oco = data.get("oco") or {}
        if all(k in oco for k in ("tp_limit","sl_trigger","sl_limit")):
            lines.append(f"TP {_fmt_int(oco['tp_limit'])}$ SLt {_fmt_int(oco['sl_trigger'])}$ SL {_fmt_int(oco['sl_limit'])}$")
        grid = data.get("grid") or {}
        for key in ("L0","L1","L2","L3"):
            if key in grid and grid[key] is not None:
                lines.append(f"{key} {_fmt_int(grid[key])}$")
    else:
        lines.append("Mode SHORT")

    return "\n".join(lines)
