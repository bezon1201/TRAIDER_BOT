import os
import json

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")


def _confyg_path(storage_dir: str = STORAGE_DIR) -> str:
    return os.path.join(storage_dir, "confyg.json")


DEFAULT_CONFIG: dict = {
    "live": False,
    "pairs": [],
}


def _read_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _normalize_config(cfg: dict | None) -> dict:
    if not isinstance(cfg, dict):
        cfg = {}
    live = bool(cfg.get("live"))
    pairs_raw = cfg.get("pairs") or []
    if not isinstance(pairs_raw, list):
        pairs_raw = []

    pairs: list[str] = []
    seen: set[str] = set()
    for p in pairs_raw:
        if not isinstance(p, str):
            continue
        s = p.upper().strip()
        if not s:
            continue
        if s not in seen:
            seen.add(s)
            pairs.append(s)

    return {"live": live, "pairs": pairs}


def load_confyg(storage_dir: str = STORAGE_DIR) -> dict:
    """
    Load confyg.json from STORAGE_DIR. If missing or invalid, create a default one.
    Always returns a normalized dict: {"live": bool, "pairs": [SYMBOL,...]}.
    """
    path = _confyg_path(storage_dir)
    raw = _read_json(path)
    if raw is None:
        cfg = _normalize_config(DEFAULT_CONFIG)
        try:
            _write_json(path, cfg)
        except Exception:
            pass
        return cfg

    cfg = _normalize_config(raw)
    try:
        _write_json(path, cfg)
    except Exception:
        # best-effort write, but config is still usable
        pass
    return cfg


def set_live_mode(is_on: bool, storage_dir: str = STORAGE_DIR) -> dict:
    """
    Update only the 'live' flag and persist the config.
    """
    cfg = load_confyg(storage_dir)
    cfg["live"] = bool(is_on)
    path = _confyg_path(storage_dir)
    try:
        _write_json(path, cfg)
    except Exception:
        pass
    return cfg


def set_live_pairs(pairs: list[str], storage_dir: str = STORAGE_DIR) -> dict:
    """
    Replace the list of live pairs, keeping the current 'live' flag.
    """
    base = load_confyg(storage_dir)
    cfg = _normalize_config({"live": base.get("live"), "pairs": pairs or []})
    path = _confyg_path(storage_dir)
    try:
        _write_json(path, cfg)
    except Exception:
        pass
    return cfg
