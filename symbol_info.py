import os, json
from coin_long_format import build_long_card
from coin_short_format import build_short_card
from confyg import load_confyg
from portfolio import get_usdc_spot_earn_total

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")


def _coin_path(symbol: str) -> str:
    return os.path.join(STORAGE_DIR, f"{symbol}.json")


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _is_live_pair(symbol: str) -> bool:
    sym = (symbol or "").upper().strip()
    if not sym:
        return False

    cfg = load_confyg()
    live = bool(cfg.get("live"))
    pairs = cfg.get("pairs") or []
    return live and sym in pairs


def build_symbol_message(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    data = _read_json(_coin_path(sym))
    data["symbol"] = sym

    mode = (data.get("trade_mode") or "").upper()
    is_live = _is_live_pair(sym)

    live_balance = None
    if is_live:
        try:
            live_balance = get_usdc_spot_earn_total(STORAGE_DIR)
        except Exception:
            live_balance = None

    if mode == "LONG":
        return build_long_card(data, is_live=is_live, live_balance=live_balance)
    elif mode == "SHORT":
        return build_short_card(data, is_live=is_live, live_balance=live_balance)
    else:
        return f"{sym}\nНет данных о режиме торговли"
