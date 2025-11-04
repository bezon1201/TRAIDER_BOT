# budget.py — budgets & flag overrides (LONG) + global cancel
# Storage: /data/<SYMBOL>.json
import os, json, re as _re, tempfile

STORAGE_DIR = os.getenv("DATA_DIR", "/data")

# ---------- basic json utils ----------
def _ensure_parent(path: str) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)

def _load_json_safe(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_json_atomic(path: str, data: dict) -> None:
    _ensure_parent(path)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _pair_json_path(symbol: str) -> str:
    return os.path.join(STORAGE_DIR, f"{symbol.upper()}.json")

# ---------- budgets ----------
def read_pair_budget(symbol: str) -> float:
    data = _load_json_safe(_pair_json_path(symbol))
    b = data.get("budget", 0)
    try:
        return float(b)
    except Exception:
        return 0.0

def write_pair_budget(symbol: str, value: float) -> float:
    data = _load_json_safe(_pair_json_path(symbol))
    try:
        v = float(value)
    except Exception:
        v = 0.0
    data["budget"] = v
    _save_json_atomic(_pair_json_path(symbol), data)
    return v

def adjust_pair_budget(symbol: str, delta: float) -> float:
    cur = read_pair_budget(symbol)
    try:
        d = float(delta)
    except Exception:
        d = 0.0
    return write_pair_budget(symbol, cur + d)

# Helper for UI header; safe no-op if caller passes already formatted text.
def apply_budget_header(symbol: str, header_text: str) -> str:
    budget = read_pair_budget(symbol)
    # append budget after SYMBOL if >0
    sym = symbol.upper()
    if budget and isinstance(header_text, str):
        if header_text.startswith(sym):
            return f"{sym} {int(budget) if budget.is_integer() else budget} {header_text[len(sym):]}"
        return f"{sym} {int(budget) if float(budget).is_integer() else budget}"
    return header_text

# ---------- flag overrides (for LONG only) ----------
# States: 'open' -> ⚠️ (order sent), 'fill' -> ✅ (order filled)
_VALID_KEYS = {"oco","l0","l1","l2","l3"}

def _read_overrides(symbol: str) -> dict:
    data = _load_json_safe(_pair_json_path(symbol))
    ov = data.get("flag_overrides")
    return ov if isinstance(ov, dict) else {}

def _write_overrides(symbol: str, overrides: dict) -> None:
    data = _load_json_safe(_pair_json_path(symbol))
    data["flag_overrides"] = overrides
    _save_json_atomic(_pair_json_path(symbol), data)

def set_flag_override(symbol: str, key: str, state: str) -> str:
    k = key.lower()
    if k not in _VALID_KEYS:
        raise ValueError("invalid key")
    st = state.lower()
    if st not in ("open","fill"):
        raise ValueError("invalid state")
    ov = _read_overrides(symbol)
    cur = (ov.get(k) or "").lower()
    if cur == "fill":  # terminal, don't downgrade
        return "fill"
    ov[k] = st
    _write_overrides(symbol, ov)
    return st

def cancel_flag_override(symbol: str, key: str) -> str:
    k = key.lower()
    if k not in _VALID_KEYS:
        raise ValueError("invalid key")
    ov = _read_overrides(symbol)
    cur = (ov.get(k) or "").lower()
    if cur == "fill":
        return "fill"
    if k in ov:
        del ov[k]
        _write_overrides(symbol, ov)
    return "auto"

def apply_flags_overrides(symbol: str, flags: dict) -> dict:
    # Apply manual overrides on top of computed flags.
    # Priority: ✅ (fill) → ⚠️ (open) → auto (🔴/🟡/🟢)
    if not isinstance(flags, dict):
        return flags
    ov = _read_overrides(symbol)
    if not ov:
        return flags
    out = dict(flags)
    def put(key_up: str, emoji: str):
        if key_up in out:
            out[key_up] = emoji
    for k, st in ov.items():
        key_up = k.upper()
        if st == "fill":
            put(key_up, "✅")
        elif st == "open":
            put(key_up, "⚠️")
    return out

# ---------- global cancel: reset budgets & overrides ----------
def _extract_symbols_from_pairs_json(pairs_obj) -> set[str]:
    syms = set()
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v)
                if isinstance(k, str) and _re.fullmatch(r"[A-Z0-9]{6,}", k):
                    syms.add(k.upper())
        elif isinstance(x, list):
            for i in x:
                walk(i)
        elif isinstance(x, str):
            if _re.fullmatch(r"[A-Z0-9]{6,}", x):
                syms.add(x.upper())
    walk(pairs_obj)
    return syms

def _list_candidate_symbols() -> set[str]:
    syms = set()
    # from pairs.json
    p_json = os.path.join(STORAGE_DIR, "pairs.json")
    try:
        with open(p_json, "r", encoding="utf-8") as f:
            syms |= _extract_symbols_from_pairs_json(json.load(f))
    except Exception:
        pass
    # from *.json filenames
    try:
        for name in os.listdir(STORAGE_DIR):
            if name.lower().endswith(".json"):
                base = name[:-5]
                if _re.fullmatch(r"[A-Z0-9]{6,}", base):
                    syms.add(base.upper())
    except Exception:
        pass
    return syms

def reset_all_budgets_and_overrides() -> int:
    syms = sorted(_list_candidate_symbols())
    cnt = 0
    for sym in syms:
        path = _pair_json_path(sym)
        data = _load_json_safe(path)
        data["budget"] = 0
        if "flag_overrides" in data:
            try:
                del data["flag_overrides"]
            except Exception:
                data["flag_overrides"] = {}
        _save_json_atomic(path, data)
        cnt += 1
    return cnt
