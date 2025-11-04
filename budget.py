
# coding: utf-8
"""
Budget & flags module for pairs.
Stores per-pair "budget" and manual flag overrides in <SYMBOL>.json files.
Exposes helpers used by app.py and metrics_runner.py.
"""

from __future__ import annotations
import json, os, glob
from typing import Dict, Any, Optional

DATA_DIR = os.environ.get("DATA_DIR", ".")

VALID_LEVELS = ["TP", "SLt", "SL", "L0", "L1", "L2", "L3"]
WARN_FLAG = "⚠️"
FILL_FLAG = "✅"

def _pair_path(symbol: str) -> str:
    sym = normalize_symbol(symbol)
    return os.path.join(DATA_DIR, f"{sym}.json")

def normalize_symbol(s: str) -> str:
    s = (s or "").strip().upper()
    # allow commands like /budget btcusdc=25
    s = s.replace("/", "")
    return s

def _load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_json(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def load_pair(symbol: str) -> Dict[str, Any]:
    """Load pair JSON (returns dict; missing file -> {})."""
    return _load_json(_pair_path(symbol))

def save_pair(symbol: str, data: Dict[str, Any]) -> None:
    _save_json(_pair_path(symbol), data)

# ---------------- Budgets ----------------

def get_budget(symbol: str) -> float:
    d = load_pair(symbol)
    v = d.get("budget", 0)
    try:
        return float(v)
    except Exception:
        return 0.0

def set_budget(symbol: str, amount: float) -> float:
    d = load_pair(symbol)
    try:
        amt = float(amount)
    except Exception:
        amt = 0.0
    d["budget"] = float(round(amt, 8))
    save_pair(symbol, d)
    return d["budget"]

def adjust_budget(symbol: str, delta: float) -> float:
    cur = get_budget(symbol)
    try:
        dv = float(delta)
    except Exception:
        dv = 0.0
    return set_budget(symbol, cur + dv)

# ---------------- Flags (manual overrides) ----------------

def _ensure_overrides(d: Dict[str, Any]) -> Dict[str, Any]:
    fo = d.get("flag_overrides")
    if not isinstance(fo, dict):
        fo = {}
        d["flag_overrides"] = fo
    return fo

def set_all_flags_warn(symbol: str) -> None:
    """Set ALL levels to WARN (⚠️). Used by 'oco open' / LONG."""
    d = load_pair(symbol)
    fo = _ensure_overrides(d)
    for lvl in VALID_LEVELS:
        fo[lvl] = WARN_FLAG
    save_pair(symbol, d)

def set_level_cancel(symbol: str, level: str) -> None:
    """Return a level flag back to automatic (remove override)."""
    lvl = level.strip()
    d = load_pair(symbol)
    fo = _ensure_overrides(d)
    if lvl in fo:
        fo.pop(lvl, None)
    save_pair(symbol, d)

def set_level_fill(symbol: str, level: str) -> None:
    """Mark a level as filled (✅)."""
    lvl = level.strip()
    d = load_pair(symbol)
    fo = _ensure_overrides(d)
    fo[lvl] = FILL_FLAG
    save_pair(symbol, d)

def cancel_all_budgets_and_flags() -> int:
    """Reset all budgets to 0 and remove all manual overrides for ALL pair jsons in DATA_DIR.
    Returns number of updated pairs.
    """
    cnt = 0
    for path in glob.glob(os.path.join(DATA_DIR, "*.json")):
        try:
            d = _load_json(path)
            changed = False
            if d.get("budget", 0) != 0:
                d["budget"] = 0
                changed = True
            if d.get("flag_overrides"):
                d["flag_overrides"] = {}
                changed = True
            if changed:
                _save_json(path, d)
                cnt += 1
        except Exception:
            continue
    return cnt

# ---------------- Application into computed flags ----------------

def apply_flags_overrides(pair: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay manual overrides onto computed flags in a pair dict.
    Expectations:
      pair["flags"] - dict with computed flags for levels, e.g. {'L0':'🟢','L1':'🟡',...}
      pair["flag_overrides"] - optional dict {'L0':'⚠️','L2':'✅', ...}
    Returns the same dict with pair['flags'] updated in place.
    """
    if not isinstance(pair, dict):
        return pair
    flags = pair.get("flags")
    if not isinstance(flags, dict):
        flags = {}
        pair["flags"] = flags
    fo = pair.get("flag_overrides") or {}
    if isinstance(fo, dict):
        for lvl, val in fo.items():
            if lvl in VALID_LEVELS and val in (WARN_FLAG, FILL_FLAG):
                flags[lvl] = val
    # Ensure only known levels exist
    for k in list(flags.keys()):
        if k not in VALID_LEVELS:
            flags.pop(k, None)
    return pair

__all__ = [
    "normalize_symbol",
    "load_pair",
    "save_pair",
    "get_budget",
    "set_budget",
    "adjust_budget",
    "set_all_flags_warn",
    "set_level_cancel",
    "set_level_fill",
    "cancel_all_budgets_and_flags",
    "apply_flags_overrides",
]
