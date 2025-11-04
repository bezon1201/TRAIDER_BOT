# -*- coding: utf-8 -*-
"""
Budget management for LONG (core) coins.
"""

from __future__ import annotations
import os, json
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from pathlib import Path

DATA_PATH = "/data"
FILE_PATH = os.path.join(DATA_PATH, "budget_long.json")

# --- helpers ---------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dtobj: datetime) -> str:
    return dtobj.astimezone(timezone.utc).replace(tzinfo=timezone.utc).isoformat().replace("+00:00","Z")

def _start_of_month(dtobj: datetime) -> datetime:
    return dtobj.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

def _end_of_month(dtobj: datetime) -> datetime:
    if dtobj.month == 12:
        nxt = dtobj.replace(year=dtobj.year+1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        nxt = dtobj.replace(month=dtobj.month+1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return nxt

def _round_int(x: float | int) -> int:
    try:
        return int(round(float(x)))
    except Exception:
        return 0

def _init_payload(tz_hours: int = 0):
    now = _now_utc()
    local = now + timedelta(hours=tz_hours)
    dow = local.weekday()  # Mon=0..Sun=6
    back = (dow - 6) % 7
    week_start_local = (local - timedelta(days=back)).replace(hour=10, minute=0, second=0, microsecond=0)
    if local < week_start_local:
        week_start_local -= timedelta(days=7)
    week_end_local = week_start_local + timedelta(days=7)
    month_start_local = _start_of_month(local)
    month_end_local = _end_of_month(local)
    week_start_utc = week_start_local - timedelta(hours=tz_hours)
    week_end_utc = week_end_local - timedelta(hours=tz_hours)
    month_start_utc = month_start_local - timedelta(hours=tz_hours)
    month_end_utc = month_end_local - timedelta(hours=tz_hours)
    week_number = int(week_start_local.isocalendar().week)
    return {
        "tz_hours": tz_hours,
        "week_start_utc": _iso(week_start_utc),
        "week_end_utc": _iso(week_end_utc),
        "month_start_utc": _iso(month_start_utc),
        "month_end_utc": _iso(month_end_utc),
        "week_number": week_number,
        "symbols": {}
    }

def _load():
    if not os.path.isdir(DATA_PATH):
        os.makedirs(DATA_PATH, exist_ok=True)
    if not os.path.exists(FILE_PATH):
        return _init_payload()
    try:
        with open(FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _init_payload()

def _save(payload):
    if not os.path.isdir(DATA_PATH):
        os.makedirs(DATA_PATH, exist_ok=True)
    tmp = FILE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FILE_PATH)

# --- alloc helpers ----------------------------------------------------------

def _blank_symbol():
    return {"weekly": 0, "monthly": 0, "legs": _alloc_default(0, 0)}

def _alloc_default(weekly: int, monthly: int):
    weekly = int(weekly or 0); monthly = int(monthly or 0)
    oco = _round_int(weekly * 0.20)
    l0  = _round_int(weekly * 0.20)
    l1 = _round_int(monthly * 0.40)
    l2 = _round_int(monthly * 0.20)
    l3 = 0
    return {
        "OCO": {"left": oco, "spent": 0},
        "L0":  {"left": l0,  "spent": 0},
        "L1":  {"left": l1,  "spent": 0},
        "L2":  {"left": l2,  "spent": 0},
        "L3":  {"left": l3,  "spent": 0},
    }

def _ensure_legs(s: dict) -> dict:
    legs = s.get("legs") or {}
    for k in ("OCO","L0","L1","L2","L3"):
        if k not in legs:
            legs[k] = {"left": 0, "spent": 0}
        legs[k]["left"] = int(legs[k].get("left", 0) or 0)
        legs[k]["spent"] = int(legs[k].get("spent", 0) or 0)
    s["legs"] = legs
    s["weekly"] = int(s.get("weekly", 0) or 0)
    s["monthly"] = int(s.get("monthly", s["weekly"]*4) or 0)
    return s

# --- public API -------------------------------------------------------------

def init_if_needed(*_args, **_kwargs):
    """Инициализация бюджета при первом запуске. Совместима с вызовами из app/scheduler."""
    if not os.path.exists(FILE_PATH):
        _save(_init_payload(tz_hours=0))

def set_timezone(tz_hours: int):
    p = _init_payload(tz_hours=tz_hours)
    _save(p)
    return f"TZ set to UTC{tz_hours:+d}"

def set_weekly(symbol: str, weekly_amount: int):
    p = _load()
    s = p["symbols"].setdefault(symbol.upper(), _blank_symbol())
    s["weekly"] = _round_int(weekly_amount)
    if not s.get("monthly"):
        s["monthly"] = s["weekly"] * 4
    s["legs"] = _alloc_default(s["weekly"], s["monthly"])
    _save(p)
    return f"{symbol.upper()} weekly budget set"

def add_weekly(symbol: str, delta: int):
    p = _load()
    s = p["symbols"].setdefault(symbol.upper(), _blank_symbol())
    s["weekly"] = max(0, _round_int(s.get("weekly", 0) + delta))
    s["legs"] = _alloc_default(s["weekly"], s.get("monthly", s["weekly"]*4))
    _save(p)
    return f"{symbol.upper()} weekly budget changed by {int(delta)}"

def spend(symbol: str, leg: str, amount: int):
    p = _load()
    sym, leg = symbol.upper(), leg.upper()
    if sym not in p["symbols"]:
        return f"{sym} not found in budget"
    s = _ensure_legs(p["symbols"][sym])
    amt = max(0, _round_int(amount))
    s["legs"][leg]["spent"] += amt
    s["legs"][leg]["left"] = max(0, s["legs"][leg]["left"] - amt)
    _save(p)
    return f"{sym} {leg} spent +{amt}"

def manual_reset():
    p = _load(); tz = int(p.get("tz_hours", 0))
    newp = _init_payload(tz_hours=tz)
    for sym, s in p.get("symbols", {}).items():
        s = _ensure_legs(s)
        weekly, monthly = s["weekly"], s["monthly"]
        newp["symbols"][sym] = {"weekly": weekly, "monthly": monthly, "legs": _alloc_default(weekly, monthly)}
    _save(newp)
    return "Budget reset to new period start"

# --- budget summary / schedule ---------------------------------------------

def _format_symbol(sym: str, s: dict) -> str:
    s = _ensure_legs(s); W, M = s["weekly"], s["monthly"]
    legs = s["legs"]; leg = lambda k: f"{k} {legs[k]['left']}/{legs[k]['spent']}"
    return "\n".join([
        f"{sym}  W {W}  M {M}",
        f"{leg('OCO')} {leg('L0')}",
        f"{leg('L1')} {leg('L2')} {leg('L3')}"
    ])

def budget_per_symbol_texts(symbols: Optional[List[str]]=None) -> List[str]:
    p = _load(); syms = list(p.get("symbols", {}).keys())
    if symbols: syms = [s for s in syms if s.upper() in {x.upper() for x in symbols}]
    return [_format_symbol(sym, _ensure_legs(p["symbols"][sym])) for sym in sorted(syms)]

def budget_summary() -> str:
    p = _load(); blocks = budget_per_symbol_texts()
    tW = sum(_ensure_legs(s)["weekly"] for s in p.get("symbols", {}).values())
    tM = sum(_ensure_legs(s)["monthly"] for s in p.get("symbols", {}).values())
    week = int(p.get("week_number", 0))
    return ("\n\n".join(blocks) if blocks else "(no core pairs)") + f"\n\nTotal weekly: {tW}\nTotal monthly: {tM}\nWeek: {week}"

def budget_schedule_text() -> str:
    p = _load(); tz = int(p.get("tz_hours", 0))
    return f"Week: {p['week_start_utc']} → {p['week_end_utc']}\nMonth: {p['month_start_utc']} → {p['month_end_utc']}\nTZ: UTC{tz:+d}"

def budget_numbers_for_symbol(symbol: str) -> dict:
    p = _load(); s = p.get("symbols", {}).get(symbol.upper())
    if not s:
        return {"weekly": 0, "monthly": 0, "legs": {k: {"left": 0, "spent": 0} for k in ("OCO","L0","L1","L2","L3")}}
    s = _ensure_legs(s)
    return {"weekly": s["weekly"], "monthly": s["monthly"], "legs": s["legs"]}

# --- compatibility for scheduler -------------------------------------------

DATA_DIR = Path("/data"); BUDGET_FILE = DATA_DIR / "budget_long.json"

def _default_state(): return {"tz_offset":0,"week_number":None,"symbols":{}}

def load_state():
    try:
        if not BUDGET_FILE.exists():
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            state=_default_state(); json.dump(state, open(BUDGET_FILE,"w"), indent=2)
            return state
        return json.load(open(BUDGET_FILE,"r"))
    except Exception:
        return _default_state()

def save_state(state:dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json.dump(state, open(BUDGET_FILE,"w"), indent=2)
