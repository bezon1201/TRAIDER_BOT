
import os, json
from coin_long_format import build_long_card
from coin_short_format import build_short_card

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")


SETTINGS_PATH = os.path.join(STORAGE_DIR, "settings.json")

def _load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"LIVE_MODE": False, "LIVE_SYMBOLS": []}


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
    st = _load_settings()
    is_live = bool(st.get("LIVE_MODE")) and sym in (st.get("LIVE_SYMBOLS") or [])
    data["live"] = bool(is_live)
    mode = (data.get("trade_mode") or "").upper()
    if mode == "LONG":
        return build_long_card(data)
    elif mode == "SHORT":
        return build_short_card(data)
    else:
        return f"{sym}\nНет данных о режиме торговли"
