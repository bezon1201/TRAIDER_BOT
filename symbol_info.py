import os, json
from coin_long_format import build_long_card as build_virtual_long_card
from coin_short_format import build_short_card as build_virtual_short_card
from live_coin_long_format import build_long_card as build_live_long_card
from live_coin_short_format import build_short_card as build_live_short_card
from confyg import load_confyg

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")


def _coin_path(symbol: str) -> str:
    return os.path.join(STORAGE_DIR, f"{symbol}.json")


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _is_live_pair(sym: str) -> bool:
    """
    Проверяет, можно ли торговать символ в LIVE:
    - глобальный флаг live == True
    - символ есть в списке pairs.
    """
    try:
        cfg = load_confyg()
    except Exception:
        cfg = {}
    live_on = bool((cfg or {}).get("live"))
    pairs = cfg.get("pairs") or []
    try:
        live_pairs = {str(s).upper().strip() for s in pairs if isinstance(s, str)}
    except Exception:
        live_pairs = set()
    return bool(live_on and sym in live_pairs)


def build_symbol_message(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    data = _read_json(_coin_path(sym))
    data["symbol"] = sym
    mode = (data.get("trade_mode") or "").upper()

    is_live = _is_live_pair(sym)

    if mode == "LONG":
        if is_live:
            return build_live_long_card(data)
        return build_virtual_long_card(data)
    elif mode == "SHORT":
        if is_live:
            return build_live_short_card(data)
        return build_virtual_short_card(data)
    else:
        return f"{sym}\nНет данных о режиме торговли"
