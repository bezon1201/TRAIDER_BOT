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
