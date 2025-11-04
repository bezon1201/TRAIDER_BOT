import os
import re
import json
from pathlib import Path
from typing import Tuple, Optional, Dict, Any

DATA_DIR = Path(os.getenv("STORAGE_DIR", "."))

FLAG_OK = "🟢"
FLAG_MAYBE = "🟡"
FLAG_STOP = "🔴"
FLAG_SENT = "⚠️"
FLAG_FILLED = "✅"

LEVEL_KEYS = ["TP", "SLt", "SL", "L0", "L1", "L2", "L3"]  # names as shown on cards
OVERRIDE_KEYS = ["TP", "SLt", "SL", "L0", "L1", "L2", "L3"]

def normalize_symbol(s: str) -> str:
    s = s.strip()
    if s.startswith("/"):
        s = s[1:]
    return s.replace("-", "").replace("_", "").upper()

def _pair_path(sym: str) -> Path:
    return DATA_DIR / f"{sym}.json"

def _read_pair(sym: str) -> Dict[str, Any]:
    p = _pair_path(sym)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _write_pair(sym: str, data: Dict[str, Any]) -> None:
    p = _pair_path(sym)
    p.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

def get_budget_for_symbol(sym: str) -> Optional[int]:
    sym = normalize_symbol(sym)
    d = _read_pair(sym)
    b = d.get("budget")
    if isinstance(b, (int, float)):
        return int(b)
    return 0

# ---- parsing ----

class BudgetCmd:
    kind: str
    sym: Optional[str]
    value: Optional[int]
    level: Optional[str]

    def __init__(self, kind: str, sym: Optional[str] = None, value: Optional[int] = None, level: Optional[str] = None):
        self.kind = kind
        self.sym = sym
        self.value = value
        self.level = level

def parse_budget_command(text: str) -> BudgetCmd:
    t = text.strip()
    if not t.lower().startswith("/budget"):
        raise ValueError("not a budget command")
    payload = t[len("/budget"):].strip()

    if payload.lower() == "cancel":
        return BudgetCmd("cancel_all")

    # /budget btcusdc=25
    m = re.match(r"^([a-z0-9_/\-]+)\s*=\s*([0-9]+)\s*$", payload, re.I)
    if m:
        return BudgetCmd("set", normalize_symbol(m.group(1)), int(m.group(2)))

    # /budget btceth + 5  or /budget btceth - 7
    m = re.match(r"^([a-z0-9_/\-]+)\s*([+\-])\s*([0-9]+)\s*$", payload, re.I)
    if m:
        sign = 1 if m.group(2) == "+" else -1
        return BudgetCmd("inc", normalize_symbol(m.group(1)), sign * int(m.group(3)))

    # /budget btcusdc oco open
    m = re.match(r"^([a-z0-9_/\-]+)\s*oco\s*open\s*$", payload, re.I)
    if m:
        return BudgetCmd("oco_open", normalize_symbol(m.group(1)))

    # /budget ethusdc L2 cancel
    m = re.match(r"^([a-z0-9_/\-]+)\s*(L[0-3]|TP|SLT|SL)\s*cancel\s*$", payload, re.I)
    if m:
        lvl = m.group(2).upper()
        return BudgetCmd("level_cancel", normalize_symbol(m.group(1)), level=lvl)

    # /budget btcusdc L0 fill
    m = re.match(r"^([a-z0-9_/\-]+)\s*(L[0-3]|TP|SLT|SL)\s*fill\s*$", payload, re.I)
    if m:
        lvl = m.group(2).upper()
        return BudgetCmd("level_fill", normalize_symbol(m.group(1)), level=lvl)

    # Fallback help
    return BudgetCmd("help")

def _ensure_overrides(d: Dict[str, Any]) -> Dict[str, str]:
    ov = d.get("flag_overrides")
    if not isinstance(ov, dict):
        ov = {}
        d["flag_overrides"] = ov
    return ov

def _apply_set(sym: str, value: int) -> str:
    d = _read_pair(sym)
    d["budget"] = max(0, int(value))
    _write_pair(sym, d)
    return f"{sym} budget = {d['budget']}"

def _apply_inc(sym: str, delta: int) -> str:
    d = _read_pair(sym)
    cur = int(d.get("budget") or 0)
    d["budget"] = max(0, cur + int(delta))
    _write_pair(sym, d)
    sign = "+" if delta >= 0 else ""
    return f"{sym} budget {sign}{delta} → {d['budget']}"

def _apply_oco_open(sym: str) -> str:
    d = _read_pair(sym)
    ov = _ensure_overrides(d)
    for k in OVERRIDE_KEYS:
        ov[k] = "⚠️"
    _write_pair(sym, d)
    return f"{sym} flags → ⚠️ (oco open)"

def _apply_level_cancel(sym: str, level: str) -> str:
    d = _read_pair(sym)
    ov = _ensure_overrides(d)
    ov.pop(level.upper(), None)  # remove explicit flag to return to automatic
    _write_pair(sym, d)
    return f"{sym} {level.upper()} → авто"

def _apply_level_fill(sym: str, level: str) -> str:
    d = _read_pair(sym)
    ov = _ensure_overrides(d)
    ov[level.upper()] = "✅"
    _write_pair(sym, d)
    return f"{sym} {level.upper()} → ✅"

def reset_all() -> int:
    """Drop all budgets to 0 and remove explicit flag overrides for every pair file."""
    cnt = 0
    for p in DATA_DIR.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        changed = False
        if d.get("budget"):
            d["budget"] = 0
            changed = True
        if isinstance(d.get("flag_overrides"), dict) and d["flag_overrides"]:
            d["flag_overrides"] = {}
            changed = True
        if changed:
            p.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            cnt += 1
    return cnt

def apply_budget_command(cmd: BudgetCmd) -> str:
    if cmd.kind == "help":
        return ("Формат:\n"
                "/budget <pair>=<num>\n"
                "/budget <pair> +/- <num>\n"
                "/budget <pair> oco open\n"
                "/budget <pair> <L0|L1|L2|L3|TP|SLt|SL> cancel\n"
                "/budget <pair> <L0|L1|L2|L3|TP|SLt|SL> fill\n"
                "/budget cancel")
    if cmd.kind == "cancel_all":
        n = reset_all()
        return f"Сброс флагов и бюджетов по {n} парам"
    if not cmd.sym:
        return "Не указана пара"
    sym = cmd.sym
    if cmd.kind == "set":
        return _apply_set(sym, int(cmd.value or 0))
    if cmd.kind == "inc":
        return _apply_inc(sym, int(cmd.value or 0))
    if cmd.kind == "oco_open":
        return _apply_oco_open(sym)
    if cmd.kind == "level_cancel":
        return _apply_level_cancel(sym, cmd.level or "L0")
    if cmd.kind == "level_fill":
        return _apply_level_fill(sym, cmd.level or "L0")
    return "Команда не распознана"
