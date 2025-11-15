import os
import json
from pathlib import Path

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)
TRADE_MODE_PATH = STORAGE_PATH / "trade_mode.json"

VALID_TRADE_MODES = {"sim", "live"}


def get_trade_mode() -> str:
    """Return current trade mode: 'sim' or 'live'.

    If file is missing, invalid or contains unexpected value,
    falls back to 'sim' without raising.
    """
    if not TRADE_MODE_PATH.exists():
        return "sim"

    try:
        raw = TRADE_MODE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return "sim"

    mode = str(data.get("trade_mode", "sim")).lower()
    if mode not in VALID_TRADE_MODES:
        return "sim"
    return mode


def set_trade_mode(mode: str) -> None:
    """Persist trade mode to trade_mode.json.

    Accepts only 'sim' or 'live'. Raises ValueError for anything else.
    """
    mode = str(mode).lower()
    if mode not in VALID_TRADE_MODES:
        raise ValueError(f"Invalid trade mode: {mode}")

    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    payload = {"trade_mode": mode}
    TRADE_MODE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
