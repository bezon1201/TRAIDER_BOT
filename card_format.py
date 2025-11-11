import os
import json
from typing import Tuple, Dict

MODE_FILE_NAME = "mode.json"

def _storage_path(storage_dir: str) -> str:
    try:
        os.makedirs(storage_dir, exist_ok=True)
    except Exception:
        pass
    return os.path.join(storage_dir, MODE_FILE_NAME)

def load_mode(storage_dir: str) -> str:
    """
    Returns "live" or "sim". Default is "sim" if file missing or invalid.
    """
    path = _storage_path(storage_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mode = str(data.get("mode", "sim")).casefold()
        return "live" if mode == "live" else "sim"
    except Exception:
        return "sim"

def save_mode(storage_dir: str, mode: str) -> None:
    mode_norm = "live" if str(mode).casefold() == "live" else "sim"
    path = _storage_path(storage_dir)
    payload = {
        "mode": mode_norm,
        "updated_utc": __utc_now_iso(),
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        # best-effort
        pass

def __utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def render_main_card(current_mode: str) -> Tuple[str, Dict]:
    """
    Returns (text, inline_keyboard_reply_markup_dict)
    """
    mode = (current_mode or "sim").casefold()
    if mode == "live":
        text = "✅LIVE"
    else:
        text = "❌SIMULATION"
    kb = {
        "inline_keyboard": [
            [ {"text": "MODE", "callback_data": "a=mode_menu"} ]
        ]
    }
    return text, kb

def render_mode_menu(current_mode: str) -> Tuple[str, Dict]:
    """
    Returns (text, inline_keyboard_reply_markup_dict) for the mode submenu.
    Buttons are fixed as per spec: [✅LIVE] [❌SIMULATION]
    """
    text = "MODE"
    kb = {
        "inline_keyboard": [
            [
                {"text": "✅LIVE", "callback_data": "a=mode_set;v=live"},
                {"text": "❌SIMULATION", "callback_data": "a=mode_set;v=sim"}
            ]
        ]
    }
    return text, kb

def render_mode_confirm(new_mode: str) -> Tuple[str, Dict]:
    human = "LIVE" if (new_mode or "sim").casefold() == "live" else "SIMULATION"
    text = f"Режим переключён: {human}"
    kb = {
        "inline_keyboard": [
            [
                {"text": "✅", "callback_data": "a=mode_back"},
                {"text": "↩️", "callback_data": "a=mode_back"}
            ]
        ]
    }
    return text, kb
