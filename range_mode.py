
import os, json
from typing import Tuple, List

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

def _pairs_path() -> str:
    return os.path.join(STORAGE_DIR, "pairs.json")

def _coin_path(symbol: str) -> str:
    os.makedirs(STORAGE_DIR, exist_ok=True)
    return os.path.join(STORAGE_DIR, f"{symbol}.json")

def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def _write_json_atomic(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",",":"))
    os.replace(tmp, path)

def _normalize_symbol(s: str) -> str:
    return (s or "").strip().upper()

def _ensure_coin_file(symbol: str) -> dict:
    path = _coin_path(symbol)
    data = _read_json(path)
    if not isinstance(data, dict):
        data = {}
    if not data.get("symbol"):
        data["symbol"] = symbol
    if not data.get("trade_mode"):
        data["trade_mode"] = "SHORT"
    _write_json_atomic(path, data)
    return data

def list_pairs() -> List[str]:
    data = _read_json(_pairs_path())
    if isinstance(data, list):
        res=[]; seen=set()
        for x in data:
            s=_normalize_symbol(str(x))
            if s and s not in seen:
                seen.add(s); res.append(s)
        return res
    return []

def get_mode(symbol: str) -> Tuple[str, str]:
    sym = _normalize_symbol(symbol)
    if not sym:
        return "", ""
    data = _ensure_coin_file(sym)
    mode = str(data.get("trade_mode") or "SHORT").upper()
    if mode not in ("LONG","SHORT"):
        mode = "SHORT"
        data["trade_mode"] = mode
        _write_json_atomic(_coin_path(sym), data)
    return sym, mode

def set_mode(symbol: str, mode: str) -> Tuple[str, str]:
    sym = _normalize_symbol(symbol)
    md = (mode or "").strip().upper()
    if md not in ("LONG","SHORT"):
        raise ValueError("Некорректный режим")
    data = _ensure_coin_file(sym)
    data["trade_mode"] = md
    _write_json_atomic(_coin_path(sym), data)
    return sym, md

def list_modes() -> str:
    pairs = list_pairs()
    items = []
    for p in pairs:
        _, m = get_mode(p)
        items.append(f"{p}={m}")
    return ", ".join(items) if items else "—"
