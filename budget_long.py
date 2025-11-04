# -*- coding: utf-8 -*-
"""
Budget management for LONG (core) coins.

Правила:
- ОCO и L0: недельные. Если за неделю не исчерпаны, на следующую неделю
  переносятся остатки + добавляется бонус 1/4 от базового недельного бюджета
  данной ноги. В конце месяца остаток считаем купленным по рынку (spent += left),
  left -> 0 и на новую неделю выдаём базу.
- L1, L2, L3: месячные. Стоят весь месяц. В конце месяца остаток переносится
  на следующий месяц (left_new = base + carry), spent -> 0.
- Неделя: воскресенье 10:00 местного времени (tz_hours) → +7 дней.
- Храним всё в /data/budget_long.json
"""

from __future__ import annotations
import os, json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

DATA_DIR = Path("/data")
FILE_PATH = DATA_DIR / "budget_long.json"

# ---------------------------- time helpers ---------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dtobj: datetime) -> str:
    return dtobj.astimezone(timezone.utc).replace(tzinfo=timezone.utc).isoformat().replace("+00:00","Z")

def _start_of_month_local(local: datetime) -> datetime:
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

def _start_of_next_month_local(local: datetime) -> datetime:
    if local.month == 12:
        return local.replace(year=local.year+1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return local.replace(month=local.month+1, day=1, hour=0, minute=0, second=0, microsecond=0)

def _calc_windows(tz_hours: int, ref_utc: Optional[datetime]=None) -> Dict[str, str]:
    if ref_utc is None:
        ref_utc = _now_utc()
    local = ref_utc + timedelta(hours=tz_hours)

    # Неделя: вс 10:00 (Mon=0..Sun=6)
    dow = local.weekday()
    back = (dow - 6) % 7
    week_start_local = (local - timedelta(days=back)).replace(hour=10, minute=0, second=0, microsecond=0)
    if local < week_start_local:
        week_start_local -= timedelta(days=7)
    week_end_local = week_start_local + timedelta(days=7)

    # Месяц: локальные границы календарного месяца
    month_start_local = _start_of_month_local(local)
    month_end_local = _start_of_next_month_local(local)

    return {
        "week_start_utc": _iso(week_start_local - timedelta(hours=tz_hours)),
        "week_end_utc":   _iso(week_end_local   - timedelta(hours=tz_hours)),
        "month_start_utc": _iso(month_start_local - timedelta(hours=tz_hours)),
        "month_end_utc":   _iso(month_end_local   - timedelta(hours=tz_hours)),
        "week_number": int(week_start_local.isocalendar().week),
    }

# ---------------------------- io helpers -----------------------------------

def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

def _load() -> Dict[str, Any]:
    _ensure_dir()
    if not FILE_PATH.exists():
        return _init_payload(0)
    try:
        return json.load(open(FILE_PATH, "r", encoding="utf-8"))
    except Exception:
        return _init_payload(0)

def _save(payload: Dict[str, Any]):
    _ensure_dir()
    tmp = FILE_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FILE_PATH)

# ---------------------------- math helpers ---------------------------------

def _ri(x) -> int:
    try:
        return int(round(float(x)))
    except Exception:
        return 0

def _alloc_base(weekly: int, monthly: int) -> Dict[str, Dict[str,int]]:
    # Все числа — целые
    oco = _ri(weekly * 0.20)
    l0  = _ri(weekly * 0.20)
    l1  = _ri(monthly * 0.40)
    l2  = _ri(monthly * 0.20)
    l3  = 0
    return {
        "OCO": {"left": oco, "spent": 0},
        "L0":  {"left": l0,  "spent": 0},
        "L1":  {"left": l1,  "spent": 0},
        "L2":  {"left": l2,  "spent": 0},
        "L3":  {"left": l3,  "spent": 0},
    }

def _blank_symbol() -> Dict[str, Any]:
    return {"weekly": 0, "monthly": 0, "legs": _alloc_base(0,0)}

def _ensure_legs(s: Dict[str, Any]) -> Dict[str, Any]:
    legs = s.get("legs") or {}
    for k in ("OCO","L0","L1","L2","L3"):
        node = legs.get(k) or {}
        node["left"]  = int(node.get("left", 0) or 0)
        node["spent"] = int(node.get("spent", 0) or 0)
        legs[k] = node
    s["legs"] = legs
    s["weekly"]  = int(s.get("weekly", 0) or 0)
    s["monthly"] = int(s.get("monthly", s["weekly"]*4) or 0)
    return s

# ---------------------------- payload init ---------------------------------

def _init_payload(tz_hours: int) -> Dict[str, Any]:
    win = _calc_windows(tz_hours)
    return {
        "tz_hours": tz_hours,
        "week_start_utc": win["week_start_utc"],
        "week_end_utc":   win["week_end_utc"],
        "month_start_utc": win["month_start_utc"],
        "month_end_utc":   win["month_end_utc"],
        "week_number": win["week_number"],
        "symbols": {}
    }

# ---------------------------- public API -----------------------------------

def init_if_needed(*_args, **_kwargs):
    """Создать файл бюджета, если отсутствует (совместимый вызов)."""
    _ensure_dir()
    if not FILE_PATH.exists():
        _save(_init_payload(0))

def set_timezone(tz_hours: int):
    tz_hours = int(tz_hours)
    payload = _init_payload(tz_hours)
    _save(payload)
    return f"TZ set to UTC{tz_hours:+d}"

def set_weekly(symbol: str, weekly_amount: int):
    payload = _load()
    sym = symbol.upper()
    s = payload["symbols"].setdefault(sym, _blank_symbol())
    s["weekly"] = max(0, _ri(weekly_amount))
    if not s.get("monthly"):
        s["monthly"] = s["weekly"] * 4
    s["legs"] = _alloc_base(s["weekly"], s["monthly"])
    _save(payload)
    return f"{sym} weekly budget set"

def add_weekly(symbol: str, delta: int):
    payload = _load()
    sym = symbol.upper()
    s = payload["symbols"].setdefault(sym, _blank_symbol())
    s["weekly"] = max(0, _ri(s.get("weekly", 0) + delta))
    if not s.get("monthly"):
        s["monthly"] = s["weekly"] * 4
    s["legs"] = _alloc_base(s["weekly"], s["monthly"])
    _save(payload)
    return f"{sym} weekly budget changed by {int(delta)}"

def spend(symbol: str, leg: str, amount: int):
    payload = _load()
    sym, leg = symbol.upper(), leg.upper()
    if sym not in payload["symbols"]:
        return f"{sym} not found in budget"
    s = _ensure_legs(payload["symbols"][sym])
    amt = max(0, _ri(amount))
    s["legs"][leg]["spent"] += amt
    s["legs"][leg]["left"] = max(0, s["legs"][leg]["left"] - amt)
    _save(payload)
    return f"{sym} {leg} spent +{amt}"

def manual_reset():
    """Полный ручной сброс: как будто начало 1-й недели и 1-го дня месяца."""
    p = _load()
    tz = int(p.get("tz_hours", 0))
    newp = _init_payload(tz)
    for sym, s in p.get("symbols", {}).items():
        s = _ensure_legs(s)
        W, M = s["weekly"], s["monthly"]
        newp["symbols"][sym] = {"weekly": W, "monthly": M, "legs": _alloc_base(W, M)}
    _save(newp)
    return "Budget reset to new period start"

# ---------------------------- summaries ------------------------------------

def _fmt_sym(sym: str, s: Dict[str, Any]) -> str:
    s = _ensure_legs(s)
    W, M = s["weekly"], s["monthly"]
    L = s["legs"]
    leg = lambda k: f"{k} {L[k]['left']}/{L[k]['spent']}"
    return "\n".join([
        f"{sym}  W {W}  M {M}",
        f"{leg('OCO')} {leg('L0')}",
        f"{leg('L1')} {leg('L2')} {leg('L3')}",
    ])

def budget_per_symbol_texts(symbols: Optional[List[str]] = None) -> List[str]:
    p = _load()
    syms = list(p.get("symbols", {}).keys())
    if symbols:
        want = {x.upper() for x in symbols}
        syms = [s for s in syms if s.upper() in want]
    return [_fmt_sym(sym, _ensure_legs(p["symbols"][sym])) for sym in sorted(syms)]

def budget_summary() -> str:
    p = _load()
    blocks = budget_per_symbol_texts()
    tW = sum(_ensure_legs(s)["weekly"] for s in p.get("symbols", {}).values())
    tM = sum(_ensure_legs(s)["monthly"] for s in p.get("symbols", {}).values())
    week = int(p.get("week_number", 0))
    body = "\n\n".join(blocks) if blocks else "(no core pairs)"
    return f"{body}\n\nTotal weekly: {tW}\nTotal monthly: {tM}\nWeek: {week}"

def budget_schedule_text() -> str:
    p = _load()
    tz = int(p.get("tz_hours", 0))
    return f"Week: {p['week_start_utc']} → {p['week_end_utc']}\nMonth: {p['month_start_utc']} → {p['month_end_utc']}\nTZ: UTC{tz:+d}"

def budget_numbers_for_symbol(symbol: str) -> Dict[str, Any]:
    p = _load()
    s = p.get("symbols", {}).get(symbol.upper())
    if not s:
        return {"weekly": 0, "monthly": 0, "legs": {k: {"left": 0, "spent": 0} for k in ("OCO","L0","L1","L2","L3")}}
    s = _ensure_legs(s)
    return {"weekly": s["weekly"], "monthly": s["monthly"], "legs": s["legs"]}

# ---------------------------- scheduler API --------------------------------
# (именно эти функции ждёт scheduler.py)

def _weekly_rollover_for_leg(base: int, left: int, spent: int) -> Dict[str,int]:
    """
    Если неделя закончилась:
    - если что-то осталось (left > 0), переносим остаток + бонус 1/4 от базы;
    - если потрачено всё (left == 0), выдаём базу.
    spent всегда обнуляем на новую неделю.
    """
    bonus = _ri(base * 0.25)
    if left > 0:
        new_left = base + left + bonus
    else:
        new_left = base
    return {"left": max(0, new_left), "spent": 0}

def _month_base_legs(W: int, M: int) -> Dict[str,int]:
    """База для каждой ноги (целые)."""
    return {
        "OCO": _ri(W * 0.20),
        "L0":  _ri(W * 0.20),
        "L1":  _ri(M * 0.40),
        "L2":  _ri(M * 0.20),
        "L3":  0
    }

def weekly_tick() -> str:
    """
    Пересчёт НЕДЕЛИ:
    - Проверяем, что текущий момент >= week_end_utc.
    - ОCO/L0: применяем правило ролловера + 1/4.
    - L1/L2/L3 без изменений (месячные).
    - Обновляем окно недели и week_number.
    """
    p = _load()
    tz = int(p.get("tz_hours", 0))

    try:
        week_end = datetime.fromisoformat(p["week_end_utc"].replace("Z","+00:00"))
    except Exception:
        # если дата битая — пересчитаем окно от текущего
        win = _calc_windows(tz)
        p.update(win)
        _save(p)
        return "weekly: window recalculated"

    now = _now_utc()
    if now < week_end:
        return "weekly: no-op (not yet ended)"

    # наступила новая неделя
    win = _calc_windows(tz, ref_utc=now)
    # База для ног по каждому символу
    for sym, s in p.get("symbols", {}).items():
        s = _ensure_legs(s)
        W, M = s["weekly"], s["monthly"]
        base = _month_base_legs(W, M)

        # Weekly legs rollover
        s["legs"]["OCO"] = _weekly_rollover_for_leg(base["OCO"], s["legs"]["OCO"]["left"], s["legs"]["OCO"]["spent"])
        s["legs"]["L0"]  = _weekly_rollover_for_leg(base["L0"],  s["legs"]["L0"]["left"],  s["legs"]["L0"]["spent"])
        # Месячные ноги не трогаем

        p["symbols"][sym] = s

    p["week_start_utc"] = win["week_start_utc"]
    p["week_end_utc"]   = win["week_end_utc"]
    p["week_number"]    = win["week_number"]
    _save(p)
    return "weekly: rolled over"

def month_end_tick() -> str:
    """
    Пересчёт МЕСЯЦА:
    - ОCO/L0: считаем остаток купленным по рынку => spent += left; left = 0.
      На новую неделю база обновится естественно на первом weekly_tick.
      (Чтобы не зависали средства.)
    - L1/L2/L3: переносим остаток на следующий месяц (left_new = base + carry).
      spent -> 0.
    - Обновляем окно месяца (и неделю тоже, т.к. мог сдвинуться week_number).
    """
    p = _load()
    tz = int(p.get("tz_hours", 0))

    try:
        month_end = datetime.fromisoformat(p["month_end_utc"].replace("Z","+00:00"))
    except Exception:
        win = _calc_windows(tz)
        p.update(win)
        _save(p)
        return "monthly: window recalculated"

    now = _now_utc()
    if now < month_end:
        return "monthly: no-op (not yet ended)"

    # наступил новый месяц
    win = _calc_windows(tz, ref_utc=now)

    for sym, s in p.get("symbols", {}).items():
        s = _ensure_legs(s)
        W, M = s["weekly"], s["monthly"]
        base = _month_base_legs(W, M)

        # ОCO/L0 -> считаем купленным
        for k in ("OCO","L0"):
            left = s["legs"][k]["left"]
            s["legs"][k]["spent"] += left
            s["legs"][k]["left"] = 0  # до первой новой недели

        # Месячные L1..L3: перенос в новый месяц
        for k in ("L1","L2","L3"):
            carry = s["legs"][k]["left"]
            s["legs"][k]["left"]  = max(0, base[k] + carry)
            s["legs"][k]["spent"] = 0

        p["symbols"][sym] = s

    p["month_start_utc"] = win["month_start_utc"]
    p["month_end_utc"]   = win["month_end_utc"]
    # также синхронизируем недельное окно (на случай пересечения)
    p["week_start_utc"]  = win["week_start_utc"]
    p["week_end_utc"]    = win["week_end_utc"]
    p["week_number"]     = win["week_number"]

    _save(p)
    return "monthly: rolled over"

# ---------------------- compatibility for external code --------------------

def load_state() -> Dict[str, Any]:
    """Для обратной совместимости (metrics_runner/portfolio ожидали это API)."""
    try:
        return _load()
    except Exception:
        return {"tz_hours": 0, "symbols": {}, "week_number": 0}

def save_state(state: Dict[str, Any]):
    """Для обратной совместимости."""
    try:
        _save(state)
    except Exception:
        pass
