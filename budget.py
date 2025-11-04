import os
import json
from typing import Any

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

def _load_json_safe(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def _save_json_atomic(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)

def read_pair_budget(symbol: str) -> float:
    path = os.path.join(STORAGE_DIR, f"{symbol.upper()}.json")
    data = _load_json_safe(path)
    v = data.get("budget")
    try:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str) and v.strip():
            return float(v.strip())
    except Exception:
        pass
    return 0.0

def write_pair_budget(symbol: str, value: float) -> float:
    path = os.path.join(STORAGE_DIR, f"{symbol.upper()}.json")
    data = _load_json_safe(path)
    data["budget"] = float(value)
    _save_json_atomic(path, data)
    return float(value)

def adjust_pair_budget(symbol: str, delta: float) -> float:
    cur = read_pair_budget(symbol)
    newv = cur + float(delta)
    if newv < 0:
        newv = 0.0
    return write_pair_budget(symbol, newv)

def _fmt_budget(val: float) -> str:
    return str(int(val)) if abs(val - int(val)) < 1e-9 else str(round(val, 6))

def apply_budget_header(symbol: str, msg: str) -> str:
    """Ensure first non-empty line is 'SYMBOL <budget>'."""
    budget = read_pair_budget(symbol)
    lines = (msg or "").splitlines()
    # Find first non-empty line and insert/replace
    for i, line in enumerate(lines):
        if line.strip() == "":
            continue
        if line.strip().upper() == symbol.upper():
            lines[i] = f"{symbol.upper()} {_fmt_budget(budget)}"
            return "\n".join(lines)
        else:
            header = f"{symbol.upper()} {_fmt_budget(budget)}"
            return "\n".join([header] + lines)
    # message empty: just header
    return f"{symbol.upper()} {_fmt_budget(budget)}"


# ---------- flag overrides (for LONG) ----------
# States per key: 'open' -> ⚠️, 'fill' -> ✅
# Stored under JSON key 'flag_overrides': {"oco": "open", "L0": "fill", ...}

def _pair_json_path(symbol: str) -> str:
    return os.path.join(STORAGE_DIR, f"{symbol.upper()}.json")

def _load_pair_json(symbol: str) -> dict:
    return _load_json_safe(_pair_json_path(symbol))

def _save_pair_json(symbol: str, data: dict) -> None:
    _save_json_atomic(_pair_json_path(symbol), data)

def read_overrides(symbol: str) -> dict:
    data = _load_pair_json(symbol)
    ov = data.get("flag_overrides")
    return ov if isinstance(ov, dict) else {}

def write_overrides(symbol: str, overrides: dict) -> None:
    data = _load_pair_json(symbol)
    data["flag_overrides"] = overrides
    _save_pair_json(symbol, data)

_VALID_KEYS = {"oco","l0","l1","l2","l3"}

def set_flag_override(symbol: str, key: str, state: str) -> str:
    """Set override for a key.
    state in {'open','fill'}; returns resulting state: 'open','fill'.
    If existing is 'fill', keep as 'fill' (no downgrade).
    """
    k = key.lower()
    if k not in _VALID_KEYS:
        raise ValueError("invalid key")
    st = state.lower()
    if st not in ("open","fill"):
        raise ValueError("invalid state")
    ov = read_overrides(symbol)
    cur = (ov.get(k) or "").lower()
    if cur == "fill":
        # terminal; don't change
        return "fill"
    ov[k] = st
    write_overrides(symbol, ov)
    return st

def cancel_flag_override(symbol: str, key: str) -> str:
    """Cancel manual override for key.
    If current is 'fill' -> keep (no change). If 'open' -> remove.
    Returns resulting state label: 'auto' | 'fill'.
    """
    k = key.lower()
    if k not in _VALID_KEYS:
        raise ValueError("invalid key")
    ov = read_overrides(symbol)
    cur = (ov.get(k) or "").lower()
    if cur == "fill":
        return "fill"
    if k in ov:
        del ov[k]
        write_overrides(symbol, ov)
    return "auto"

def apply_flags_overrides(symbol: str, flags: dict) -> dict:
    """Apply overrides to given flags dict per key.
    'fill' -> ✅, 'open' -> ⚠️, else keep automatic flag.
    """
    if not isinstance(flags, dict):
        return flags
    ov = read_overrides(symbol)
    out = dict(flags)
    for k, st in (ov or {}).items():
        k_up = k.upper() if k.upper() in ("OCO","L0","L1","L2","L3") else k
        if st == "fill":
            out[k_up if k_up in out else k] = "✅"
        elif st == "open":
            out[k_up if k_up in out else k] = "⚠️"
    return out
