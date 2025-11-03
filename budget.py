import os, json, re

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")
BUDGET_PATH = os.path.join(STORAGE_DIR, "budget.json")
PAIRS_PATH  = os.path.join(STORAGE_DIR, "pairs.json")

def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _write_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)

def _allowed_symbols():
    pairs = _read_json(PAIRS_PATH) or {}
    arr = pairs if isinstance(pairs, list) else pairs.get("pairs") or pairs.get("symbols") or []
    return {str(x).upper() for x in arr}

def _load_budget() -> dict:
    data = _read_json(BUDGET_PATH)
    return data if isinstance(data, dict) else {}

def _save_budget(bud: dict) -> dict:
    allow = _allowed_symbols()
    cleaned = {}
    for k, v in (bud or {}).items():
        sym = str(k).upper()
        if sym in allow:
            try:
                cleaned[sym] = round(max(0.0, float(v)), 2)
            except Exception:
                pass
    _write_json(BUDGET_PATH, cleaned)
    return cleaned

def _fmt(sym: str, val) -> str:
    try:
        return f"{sym} {round(float(val), 2)}"
    except Exception:
        return f"{sym} 0"

def handle_budget_command(text_norm: str) -> str:
    """
    /budget
    /budget btcusdc=100
    /budget btcusdc+50
    /budget btcusdc-25
    """
    allow   = _allowed_symbols()
    budgets = _load_budget()

    parts = text_norm.strip().split(maxsplit=1)

    # Показать все бюджеты
    if len(parts) == 1:
        if not budgets:
            return "Budget: empty"
        lines = ["Budget:"] + [_fmt(sym, budgets[sym]) for sym in sorted(budgets)]
        return "\n".join(lines)

    # sym=val | sym+val | sym-val
    arg = parts[1].strip()
    m = re.match(r'^([A-Za-z0-9:_-]+)\s*([=+\-])\s*([0-9]+(?:\.[0-9]+)?)$', arg)
    if not m:
        return "Budget: invalid format. Use /budget SYMBOL=100 or /budget SYMBOL+50 or /budget SYMBOL-25"

    sym, op, sval = m.group(1).upper(), m.group(2), m.group(3)

    if sym not in allow:
        return f"{sym} is not in pairs.json. Skipped."

    try:
        val = float(sval)
    except Exception:
        return "Budget: amount is not a number."

    cur = float(budgets.get(sym, 0.0))
    if op == "=":
        newv = val
    elif op == "+":
        newv = cur + val
    else:
        newv = cur - val

    budgets[sym] = round(max(0.0, newv), 2)
    budgets = _save_budget(budgets)
    return f"Budget set: {_fmt(sym, budgets.get(sym, 0.0))}"
