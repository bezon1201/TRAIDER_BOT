import os
import json
from typing import Optional, Dict, Any

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


def get_pair_budget(symbol: str, month: str) -> Dict[str, int]:
    sym = _norm_symbol(symbol)
    mkey = str(month)
    data = _load_budget()
    pairs = data.get("pairs") or {}
    p = pairs.get(sym, {})
    monthly = p.get("monthly") or {}
    cur = monthly.get(mkey) or {}
    budget = int(cur.get("budget") or 0)
    spent = int(cur.get("spent") or 0)
    return {"symbol": sym, "month": mkey, "budget": max(budget, 0), "spent": max(spent, 0)}


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
    cur = monthly.get(mkey) or {}
    spent = int(cur.get("spent") or 0)
    if spent < 0:
        spent = 0
    monthly[mkey] = {"budget": bval, "spent": spent}
    _save_budget(data)
    return {"symbol": sym, "month": mkey, "budget": bval, "spent": spent}


def clear_pair_budget(symbol: str, month: str) -> Dict[str, int]:
    sym = _norm_symbol(symbol)
    mkey = str(month)
    data = _load_budget()
    pairs = data.setdefault("pairs", {})
    p = pairs.setdefault(sym, {})
    monthly = p.setdefault("monthly", {})
    monthly[mkey] = {"budget": 0, "spent": 0}
    _save_budget(data)
    return {"symbol": sym, "month": mkey, "budget": 0, "spent": 0}


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
    # expect keys: mode, symbol, month
    sym = _norm_symbol(entry.get("symbol", ""))
    month = str(entry.get("month") or "")
    mode = str(entry.get("mode") or "").upper()
    if not sym or not month or mode not in ("SET",):
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
