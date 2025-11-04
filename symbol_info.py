
import os, json
from coin_long_format import build_long_card
from budget import apply_flags_overrides
from coin_short_format import build_short_card

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

def _coin_path(symbol: str) -> str:
    return os.path.join(STORAGE_DIR, f"{symbol}.json")

def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def build_symbol_message(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    data = _read_json(_coin_path(sym))
    data["symbol"] = sym
    # Apply manual overrides to flags at render-time to avoid delays
    if isinstance(data.get("flags"), dict):
        data["flags"] = apply_flags_overrides(sym, data.get("flags"))
    mode = (data.get("trade_mode") or "").upper()
    if mode == "LONG":
        return build_long_card(data)
    elif mode == "SHORT":
        return build_short_card(data)
    else:
        return f"{sym}\nНет данных о режиме торговли"
