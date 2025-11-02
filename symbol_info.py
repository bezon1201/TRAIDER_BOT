
import os, json, math

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

def _coin_path(symbol: str) -> str:
    return os.path.join(STORAGE_DIR, f"{symbol}.json")

def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _fmt_int(v: float) -> str:
    try:
        n = int(round(float(v)))
    except Exception:
        return "-"
    return str(n)

def build_symbol_message(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    if not sym:
        return "Некорректный символ"
    path = _coin_path(sym)
    data = _read_json(path)
    if not data:
        return f"{sym}\nНет данных"
    price = data.get("price") or data.get("tf", {}).get("12h", {}).get("close_last")
    price_s = _fmt_int(price) + "$" if price is not None else "-"
    trade_mode = (data.get("trade_mode") or "").upper()
    oco = data.get("oco") if trade_mode == "LONG" else None

    lines = [sym, f"Price {price_s}", "OCO Buy"]
    if oco and all(k in oco for k in ("tp_limit","sl_trigger","sl_limit")):
        tp = _fmt_int(oco.get("tp_limit")) + "$"
        slt = _fmt_int(oco.get("sl_trigger"))
        sl = _fmt_int(oco.get("sl_limit"))
        lines.append(f"TP {tp} SLt {slt} SL {sl}$")
    else:
        lines.append("Not aplicable")
    return "\n".join(lines)
