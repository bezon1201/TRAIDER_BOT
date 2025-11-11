
import os, json

def scheduler_defaults() -> dict:
    return {
        "enabled": True,
        "period_sec": 900,
        "publish_hours": 24,
        "delay_ms": 2,
        "jitter_sec": 2,
        "last_run_utc": None,
        "next_due_utc": None,
        "last_publish_utc": None,
        "next_publish_utc": None,
    }

def load_scheduler_cfg(path: str) -> dict:
    try:
        if not os.path.exists(path):
            return scheduler_defaults()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        defaults = scheduler_defaults()
        defaults.update(data or {})
        return defaults
    except Exception:
        return scheduler_defaults()

def save_scheduler_cfg(path: str, cfg: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)

def human_period(sec: int | None) -> str:
    if not sec:
        return "n/a"
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    parts = []
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s: parts.append(f"{s}s")
    return " ".join(parts) or f"{sec}s"

def human_hours(h: int | None) -> str:
    if not h:
        return "n/a"
    d = h // 24
    rest = h % 24
    if d and rest:
        return f"{d}d {rest}h"
    if d:
        return f"{d}d"
    return f"{h}h"
