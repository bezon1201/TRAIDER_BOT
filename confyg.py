import os
import json
from typing import Any, Dict, List

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")
CONFYG_FILENAME = "confyg.json"


def _confyg_path(storage_dir: str = STORAGE_DIR) -> str:
    return os.path.join(storage_dir, CONFYG_FILENAME)


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _normalize_pairs(pairs: Any) -> List[str]:
    """
    Normalize list of symbols:
      - to strings
      - strip whitespace
      - UPPERCASE
      - de-duplicate preserving order
    """
    if not isinstance(pairs, list):
        pairs = []
    seen = set()
    out: List[str] = []
    for x in pairs:
        s = _normalize_symbol(x)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _load_raw_confyg(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _save_confyg(data: Dict[str, Any], path: str) -> None:
    tmp_path = f"{path}.tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def load_confyg(storage_dir: str = STORAGE_DIR) -> Dict[str, Any]:
    """
    Load confyg.json from STORAGE_DIR.

    Always returns a dict with keys:
      - "live": bool
      - "pairs": List[str] (UPPERCASE, de-duplicated)
    Any missing/invalid data is replaced with defaults and written back.
    """
    path = _confyg_path(storage_dir)
    raw = _load_raw_confyg(path)

    live = bool(raw.get("live", False)) if isinstance(raw, dict) else False
    pairs = _normalize_pairs(raw.get("pairs") if isinstance(raw, dict) else [])

    conf: Dict[str, Any] = {"live": live, "pairs": pairs}

    try:
        _save_confyg(conf, path)
    except Exception:
        # On write error we still return the normalized config
        pass

    return conf


def set_live_mode(is_on: bool, storage_dir: str = STORAGE_DIR) -> Dict[str, Any]:
    """
    Set the "live" flag and persist confyg.json.
    """
    conf = load_confyg(storage_dir)
    conf["live"] = bool(is_on)
    path = _confyg_path(storage_dir)
    _save_confyg(conf, path)
    return conf


def set_live_pairs(pairs: Any, storage_dir: str = STORAGE_DIR) -> Dict[str, Any]:
    """
    Replace the "pairs" list with given symbols (normalized).
    Does not change the "live" flag.
    """
    conf = load_confyg(storage_dir)
    conf["pairs"] = _normalize_pairs(pairs)
    path = _confyg_path(storage_dir)
    _save_confyg(conf, path)
    return conf
