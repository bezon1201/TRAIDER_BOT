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


def apply_budget_header(symbol: str, card: str) -> str:
    import os, json, re
    STORAGE_DIR = os.getenv("STORAGE_DIR", "./storage")
    path = os.path.join(STORAGE_DIR, f"{symbol}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            j = json.load(f) or {}
    except Exception:
        j = {}
    j = _ensure_spent_reserve(j)
    def _fmt_money(x):
        try:
            v = float(x)
        except Exception:
            return "0"
        iv = int(round(v))
        return str(iv) if abs(v - iv) < 0.05 else f"{v:.1f}"
    budget = j.get("budget", 0.0)
    reserve = (j.get("reserve") or {}).get("total", 0.0)
    spent = (j.get("spent") or {}).get("total", 0.0)
    head = f"{symbol} 💰{_fmt_money(budget)} | ⏳{_fmt_money(reserve)} | 💸{_fmt_money(spent)}"
    try:
        lines = (card or "").splitlines()
        if not lines:
            return head
        # drop original first line (symbol) and align numbers so flags start in one column
        lines = lines[1:]
        order_idx = []
        amts = []
        for i, ln in enumerate(lines):
            m = re.match(r'^\s*(\d{1,4})(.*)$', ln)
            if m and ("TP" in m.group(2) or "L0" in m.group(2) or "L1" in m.group(2) or "L2" in m.group(2) or "L3" in m.group(2)):
                order_idx.append(i)
                amts.append(int(m.group(1)))
        if amts:
            maxd = max(len(str(a)) for a in amts)
            def pad(n:int)->str:
                d = len(str(n))
                return ' ' * (2 * (maxd - d)) + str(n)
            for i in order_idx:
                m = re.match(r'^\s*(\d{1,4})(.*)$', lines[i])
                n = int(m.group(1)); rest = m.group(2)
                lines[i] = pad(n) + rest
        return "\n".join([head] + lines)
    except Exception:
        return head


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


def _ensure_spent_reserve(j: dict) -> dict:
    j = j or {}
    if not isinstance(j.get("spent"), dict):
        j["spent"] = {"total": 0.0, "by_order": {"OCO":0.0,"L0":0.0,"L1":0.0,"L2":0.0,"L3":0.0}}
    else:
        j["spent"].setdefault("total", 0.0)
        j["spent"].setdefault("by_order", {"OCO":0.0,"L0":0.0,"L1":0.0,"L2":0.0,"L3":0.0})
        for k in ("OCO","L0","L1","L2","L3"):
            j["spent"]["by_order"].setdefault(k, 0.0)
    if not isinstance(j.get("reserve"), dict):
        j["reserve"] = {"total": 0.0, "by_order": {"OCO":0.0,"L0":0.0,"L1":0.0,"L2":0.0,"L3":0.0}}
    else:
        j["reserve"].setdefault("total", 0.0)
        j["reserve"].setdefault("by_order", {"OCO":0.0,"L0":0.0,"L1":0.0,"L2":0.0,"L3":0.0})
        for k in ("OCO","L0","L1","L2","L3"):
            j["reserve"]["by_order"].setdefault(k, 0.0)
    return j


def get_pocket_amount(j: dict, order_key: str) -> float:
    try:
        k = str(order_key).upper()
        return float(((j or {}).get("pockets") or {}).get("alloc_amt", {}).get(k, 0.0) or 0.0)
    except Exception:
        return 0.0


def transition_open(j: dict, order_key: str) -> (dict, str):
    j = _ensure_spent_reserve(j or {})
    k = str(order_key).upper()
    ov = dict((j.get("flag_overrides") or {}))
    state = ov.get(k)
    if state == "open":
        cur = float(j["reserve"]["by_order"].get(k, 0.0) or 0.0)
        return j, f"Уже ⚠️, резерв {int(round(cur))}."
    budget = float(j.get("budget") or 0.0)
    spent_total = float(j["spent"]["total"] or 0.0)
    reserve_total = float(j["reserve"]["total"] or 0.0)
    amt = float(get_pocket_amount(j, k))
    if spent_total + reserve_total + amt > budget + 1e-9:
        return j, "Отказ: недостаточно бюджета для OPEN."
    j["reserve"]["by_order"][k] = float(j["reserve"]["by_order"].get(k, 0.0) or 0.0) + amt
    j["reserve"]["total"] = float(j["reserve"]["total"] or 0.0) + amt
    ov[k] = "open"
    j["flag_overrides"] = ov
    return j, None

def transition_fill(j: dict, order_key: str) -> (dict, str):
    j = _ensure_spent_reserve(j or {})
    k = str(order_key).upper()
    ov = dict((j.get("flag_overrides") or {}))
    state = ov.get(k)
    if state != "open":
        return j, "Отказ: сначала OPEN."
    amt = float(j["reserve"]["by_order"].get(k, 0.0) or 0.0)
    if amt <= 0.0:
        amt = get_pocket_amount(j, k)
    j["spent"]["by_order"][k] = float(j["spent"]["by_order"].get(k, 0.0) or 0.0) + amt
    j["spent"]["total"] = float(j["spent"]["total"] or 0.0) + amt
    j["reserve"]["total"] = max(0.0, float(j["reserve"]["total"] or 0.0) - amt)
    j["reserve"]["by_order"][k] = 0.0
    ov[k] = "fill"
    j["flag_overrides"] = ov
    return j, None

def transition_cancel_open(j: dict, order_key: str) -> (dict, str):
    j = _ensure_spent_reserve(j or {})
    k = str(order_key).upper()
    ov = dict((j.get("flag_overrides") or {}))
    state = ov.get(k)
    if state != "open":
        if state == "fill":
            return j, "Уже ✅, отмена не требуется."
        return j, "Нет открытого ордера."
    amt = float(j["reserve"]["by_order"].get(k, 0.0) or 0.0)
    j["reserve"]["total"] = max(0.0, float(j["reserve"]["total"] or 0.0) - amt)
    j["reserve"]["by_order"][k] = 0.0
    if k in ov: del ov[k]
    j["flag_overrides"] = ov if ov else {}
    return j, None
