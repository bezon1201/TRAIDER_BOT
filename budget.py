import os
import json
from typing import Any, Dict, Optional

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")
BUDGET_FILE = os.path.join(STORAGE_DIR, "budget.json")
BUDGET_STATE_FILE = os.path.join(STORAGE_DIR, "budget_state.json")


BUDGET_LEVELS_FILE = os.path.join(STORAGE_DIR, "budget_levels.json")
LEVEL_KEYS = ("OCO", "L0", "L1", "L2", "L3")


def _load_levels() -> Dict[str, Any]:
    """Load per-level budget state (reserved/spent per OCO/L0-L3)."""
    data = _load_json(BUDGET_LEVELS_FILE, {})
    if not isinstance(data, dict):
        data = {}
    if "pairs" not in data or not isinstance(data.get("pairs"), dict):
        data["pairs"] = {}
    return data


def _save_levels(data: Dict[str, Any]) -> None:
    _save_json(BUDGET_LEVELS_FILE, data)


def get_pair_levels(symbol: str, month: str) -> Dict[str, Dict[str, int]]:
    """Return per-level state for a pair/month with defaults.

    Structure:
    {
        "OCO": {"reserved": int, "spent": int},
        "L0": {...},
        ...
    }
    """
    sym = _norm_symbol(symbol)
    mkey = str(month)
    data = _load_levels()
    pairs = data.get("pairs") or {}
    p = pairs.get(sym) or {}
    monthly = p.get("monthly") or {}
    cur = monthly.get(mkey) or {}

    result: Dict[str, Dict[str, int]] = {}
    for lvl in LEVEL_KEYS:
        raw = cur.get(lvl) or {}
        try:
            reserved = int(raw.get("reserved") or 0)
        except Exception:
            reserved = 0
        try:
            spent = int(raw.get("spent") or 0)
        except Exception:
            spent = 0
        if reserved < 0:
            reserved = 0
        if spent < 0:
            spent = 0
        result[lvl] = {"reserved": reserved, "spent": spent}
    return result


def _save_pair_levels(symbol: str, month: str, levels: Dict[str, Dict[str, int]]) -> None:
    """Persist per-level state for a pair/month."""
    sym = _norm_symbol(symbol)
    mkey = str(month)
    data = _load_levels()
    pairs = data.setdefault("pairs", {})
    p = pairs.setdefault(sym, {})
    monthly = p.setdefault("monthly", {})

    entry: Dict[str, Any] = {}
    for lvl in LEVEL_KEYS:
        src = levels.get(lvl) or {}
        try:
            reserved = int(src.get("reserved") or 0)
        except Exception:
            reserved = 0
        try:
            spent = int(src.get("spent") or 0)
        except Exception:
            spent = 0
        if reserved < 0:
            reserved = 0
        if spent < 0:
            spent = 0
        entry[lvl] = {"reserved": reserved, "spent": spent}

    monthly[mkey] = entry
    _save_levels(data)


def clear_pair_levels(symbol: str, month: str) -> None:
    """Reset per-level state for a pair/month (used by BUDGET CANCEL)."""
    sym = _norm_symbol(symbol)
    mkey = str(month)
    data = _load_levels()
    pairs = data.get("pairs") or {}
    p = pairs.get(sym)
    if not p:
        return
    monthly = p.get("monthly") or {}
    if mkey in monthly:
        monthly.pop(mkey, None)
        # clean up empty structures
        if not monthly:
            p.pop("monthly", None)
        if not p.get("monthly"):
            pairs.pop(sym, None)
        _save_levels(data)


def recompute_pair_aggregates(symbol: str, month: str) -> Dict[str, int]:
    """Recalculate total reserve/spent in budget.json from per-level state."""
    sym = _norm_symbol(symbol)
    mkey = str(month)

    # read per-level state
    levels = get_pair_levels(sym, mkey)
    total_reserve = sum(int(v.get("reserved") or 0) for v in levels.values())
    total_spent = sum(int(v.get("spent") or 0) for v in levels.values())
    if total_reserve < 0:
        total_reserve = 0
    if total_spent < 0:
        total_spent = 0

    data = _load_budget()
    pairs = data.setdefault("pairs", {})
    p = pairs.setdefault(sym, {})
    monthly = p.setdefault("monthly", {})
    cur_raw = monthly.get(mkey) or {}
    norm = _normalize_entry(cur_raw)

    budget = norm["budget"]
    week = int(cur_raw.get("week") or 0)

    reserve = total_reserve
    spent = total_spent
    free = budget - reserve - spent
    if free < 0:
        free = 0

    monthly[mkey] = {
        "budget": budget,
        "reserve": reserve,
        "spent": spent,
        "week": week,
    }
    _save_budget(data)

    return {
        "symbol": sym,
        "month": mkey,
        "week": week,
        "budget": budget,
        "reserve": reserve,
        "spent": spent,
        "free": free,
    }



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
    # неделя цикла (0 = не запущен)
    week = int(cur_raw.get("week") or 0)
    return {
        "symbol": sym,
        "month": mkey,
        "week": week,
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
    # сохраняем текущую неделю цикла (по умолчанию 0)
    week = int(cur_raw.get("week") or 0)

    # обновляем только бюджет, reserve/spent не трогаем
    norm["budget"] = bval
    free = bval - norm["reserve"] - norm["spent"]
    if free < 0:
        free = 0
    norm["free"] = free

    monthly[mkey] = {
        "budget": norm["budget"],
        "reserve": norm["reserve"],
        "spent": norm["spent"],
        "week": week,
    }
    _save_budget(data)

    return {
        "symbol": sym,
        "month": mkey,
        "week": week,
        "budget": norm["budget"],
        "reserve": norm["reserve"],
        "spent": norm["spent"],
        "free": norm["free"],
    }


def set_pair_week(symbol: str, month: str, week: int) -> Dict[str, int]:
    """Установить номер недели цикла для пары/месяца, не меняя бюджет/резервы."""
    sym = _norm_symbol(symbol)
    mkey = str(month)
    wval = int(week)
    if wval < 0:
        wval = 0

    data = _load_budget()
    pairs = data.setdefault("pairs", {})
    p = pairs.setdefault(sym, {})
    monthly = p.setdefault("monthly", {})

    cur_raw = monthly.get(mkey) or {}
    norm = _normalize_entry(cur_raw)

    budget = norm["budget"]
    reserve = norm["reserve"]
    spent = norm["spent"]
    free = budget - reserve - spent
    if free < 0:
        free = 0

    monthly[mkey] = {
        "budget": budget,
        "reserve": reserve,
        "spent": spent,
        "week": wval,
    }
    _save_budget(data)

    return {
        "symbol": sym,
        "month": mkey,
        "week": wval,
        "budget": budget,
        "reserve": reserve,
        "spent": spent,
        "free": free,
    }


def clear_pair_budget(symbol: str, month: str) -> Dict[str, int]:
    """Budget CANCEL: полностью очистить состояние пары/месяца до нулей.

    - budget = 0
    - reserve = 0
    - spent = 0
    - week = 0
    """
    sym = _norm_symbol(symbol)
    mkey = str(month)

    # сбрасываем помесячное состояние уровней (OCO/L0-L3)
    clear_pair_levels(sym, mkey)

    data = _load_budget()
    pairs = data.setdefault("pairs", {})
    p = pairs.setdefault(sym, {})
    monthly = p.setdefault("monthly", {})

    # Полный сброс для указанного месяца
    budget = 0
    reserve = 0
    spent = 0
    week = 0
    free = 0

    monthly[mkey] = {
        "budget": budget,
        "reserve": reserve,
        "spent": spent,
        "week": week,
    }
    _save_budget(data)

    return {
        "symbol": sym,
        "month": mkey,
        "week": week,
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