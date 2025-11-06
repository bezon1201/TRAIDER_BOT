import os
import json
from typing import Any, Dict, Optional

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")
BUDGET_FILE = os.path.join(STORAGE_DIR, "budget.json")
BUDGET_STATE_FILE = os.path.join(STORAGE_DIR, "budget_state.json")


def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, type(default)):
            return data
    except FileNotFoundError:
        return default
    except Exception:
        return default
    return default


def _save_json(path: str, data: Any) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        # best-effort; ignore write errors here
        pass


def _norm_symbol(symbol: str) -> str:
    return (symbol or "").upper().strip()


# -------- Budget core --------


def _load_budget() -> Dict[str, Any]:
    data = _load_json(BUDGET_FILE, {"version": 1, "pairs": {}})
    if not isinstance(data, dict):
        data = {"version": 1, "pairs": {}}
    if "pairs" not in data or not isinstance(data["pairs"], dict):
        data["pairs"] = {}
    if "version" not in data:
        data["version"] = 1
    return data


def _save_budget(data: Dict[str, Any]) -> None:
    _save_json(BUDGET_FILE, data)


def _normalize_entry(raw: Dict[str, Any]) -> Dict[str, int]:
    """Upgrade legacy structure.

    Legacy: {"budget": X, "spent": Y} where spent = reserve, no real spent.
    New:    {"budget": X, "reserve": R, "spent": S}
    """
    raw = raw or {}
    budget = int(raw.get("budget") or 0)
    if "reserve" in raw:
        reserve = int(raw.get("reserve") or 0)
        spent = int(raw.get("spent") or 0)
    else:
        # legacy "spent" treated as reserve
        reserve = int(raw.get("spent") or 0)
        spent = 0
    if budget < 0:
        budget = 0
    if reserve < 0:
        reserve = 0
    if spent < 0:
        spent = 0
    free = budget - reserve - spent
    if free < 0:
        free = 0
    return {
        "budget": budget,
        "reserve": reserve,
        "spent": spent,
        "free": free,
    }


def get_pair_budget(symbol: str, month: str) -> Dict[str, int]:
    sym = _norm_symbol(symbol)
    mkey = str(month)
    data = _load_budget()
    pairs = data.get("pairs") or {}
    p = pairs.get(sym) or {}
    monthly = p.get("monthly") or {}
    cur_raw = monthly.get(mkey) or {}
    norm = _normalize_entry(cur_raw)
    return {
        "symbol": sym,
        "month": mkey,
        **norm,
    }


def set_pair_budget(symbol: str, month: str, budget: int) -> Dict[str, int]:
    sym = _norm_symbol(symbol)
    mkey = str(month)
    bval = int(budget)
    if bval < 0:
        bval = 0

    data = _load_budget()
    pairs = data.setdefault("pairs", {})
    p = pairs.setdefault(sym, {})
    monthly = p.setdefault("monthly", {})

    cur_raw = monthly.get(mkey) or {}
    norm = _normalize_entry(cur_raw)
    # update only budget, keep reserve/spent
    norm["budget"] = bval
    free = bval - norm["reserve"] - norm["spent"]
    if free < 0:
        free = 0
    norm["free"] = free

    monthly[mkey] = {
        "budget": norm["budget"],
        "reserve": norm["reserve"],
        "spent": norm["spent"],
    }
    _save_budget(data)

    return {
        "symbol": sym,
        "month": mkey,
        "budget": norm["budget"],
        "reserve": norm["reserve"],
        "spent": norm["spent"],
        "free": norm["free"],
    }


def clear_pair_budget(symbol: str, month: str) -> Dict[str, int]:
    """Budget CANCEL semantics: keep budget, reset reserve and spent to 0."""
    sym = _norm_symbol(symbol)
    mkey = str(month)
    data = _load_budget()
    pairs = data.setdefault("pairs", {})
    p = pairs.setdefault(sym, {})
    monthly = p.setdefault("monthly", {})

    cur_raw = monthly.get(mkey) or {}
    norm = _normalize_entry(cur_raw)
    # keep budget as-is, drop reserve and spent
    budget = norm["budget"]
    reserve = 0
    spent = 0
    free = budget

    monthly[mkey] = {
        "budget": budget,
        "reserve": reserve,
        "spent": spent,
    }
    _save_budget(data)

    return {
        "symbol": sym,
        "month": mkey,
        "budget": budget,
        "reserve": reserve,
        "spent": spent,
        "free": free,
    }


# -------- Input state (waiting for user budget value) --------


def _load_state() -> Dict[str, Any]:
    data = _load_json(BUDGET_STATE_FILE, {})
    if not isinstance(data, dict):
        data = {}
    return data


def _save_state(data: Dict[str, Any]) -> None:
    _save_json(BUDGET_STATE_FILE, data)


def get_budget_input(chat_id: str) -> Optional[Dict[str, str]]:
    cid = str(chat_id)
    data = _load_state()
    entry = data.get(cid)
    if not isinstance(entry, dict):
        return None
    sym = _norm_symbol(entry.get("symbol", ""))
    month = str(entry.get("month") or "")
    mode = str(entry.get("mode") or "").upper()
    if not sym or not month or mode != "SET":
        return None
    return {"mode": mode, "symbol": sym, "month": month}


def set_budget_input(chat_id: str, symbol: str, month: str) -> None:
    cid = str(chat_id)
    sym = _norm_symbol(symbol)
    mkey = str(month)
    if not sym or not mkey:
        return
    data = _load_state()
    data[cid] = {"mode": "SET", "symbol": sym, "month": mkey}
    _save_state(data)


def clear_budget_input(chat_id: str) -> None:
    cid = str(chat_id)
    data = _load_state()
    if cid in data:
        data.pop(cid, None)
        _save_state(data)
