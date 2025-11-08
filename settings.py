from pathlib import Path
import json

DATA_DIR = Path("data")
SETTINGS_PATH = DATA_DIR / "settings.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULTS = {"LIVE_MODE": False, "LIVE_SYMBOLS": []}

def load_settings():
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULTS.copy())
        return DEFAULTS.copy()
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    for k, v in DEFAULTS.items():
        data.setdefault(k, v if not isinstance(v, list) else list(v))
    return data

def save_settings(s):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_PATH.open("w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

def live_badge(symbol: str) -> str:
    s = load_settings()
    on = s.get("LIVE_MODE", False)
    wl = set(s.get("LIVE_SYMBOLS", []))
    return "LIVE✅" if on and symbol in wl else "LIVE❌"
