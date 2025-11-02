
import os, json, time
from typing import Any, Dict, List

STORAGE_DIR = os.environ.get("STORAGE_DIR", "/data")
PAIRS_FILE = os.path.join(STORAGE_DIR, "pairs.json")
MODES_FILE = os.path.join(STORAGE_DIR, "modes.json")

def ensure_storage():
    os.makedirs(STORAGE_DIR, exist_ok=True)

def load_pairs() -> List[str]:
    ensure_storage()
    if os.path.exists(PAIRS_FILE):
        try:
            data = json.load(open(PAIRS_FILE, "r", encoding="utf-8"))
            if isinstance(data, list):
                return [str(x).upper() for x in data]
        except Exception:
            pass
    env_pairs = os.environ.get("PAIRS", "")
    if env_pairs:
        return [p.strip().upper() for p in env_pairs.split(",") if p.strip()]
    return []

def read_json(path: str) -> Dict[str, Any]:
    try:
        return json.load(open(path, "r", encoding="utf-8"))
    except Exception:
        return {}

def write_json(path: str, data: Dict[str, Any]):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def get_modes() -> Dict[str, str]:
    ensure_storage()
    if os.path.exists(MODES_FILE):
        try:
            data = json.load(open(MODES_FILE, "r", encoding="utf-8"))
            if isinstance(data, dict):
                return {k.upper(): str(v).upper() for k, v in data.items()}
        except Exception:
            pass
    return {}

def set_mode(sym: str, mode: str):
    ensure_storage()
    modes = get_modes()
    if mode == "RESET":
        if sym in modes:
            del modes[sym]
    else:
        modes[sym] = mode
    write_json(MODES_FILE, modes)

def normalize_tick(value: float, tick: float, op: str) -> float:
    if tick <= 0:
        return round(value, 8)
    if op == "floor":
        steps = int(value / tick)
        return round(steps * tick, 8)
    if op == "ceil":
        steps = int(-(-value // tick))  # ceiling division for floats via floor trick
        return round(steps * tick, 8)
    return round(value, 8)
