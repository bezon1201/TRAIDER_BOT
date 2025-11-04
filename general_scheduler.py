import os, json
from collections import deque
from typing import Optional

from metrics_runner import collect_all_with_micro_jitter

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

# --- scheduler state & helpers ---
from collections import deque
_scheduler_task = None
_scheduler_logs = deque(maxlen=5000)  # in-memory log lines

SCHEDULER_STATE_FILE = os.path.join(STORAGE_DIR, "scheduler_state.json")

def _sched_log(*args):
    msg = " ".join(str(a) for a in args)
    try:
        print("[scheduler]", msg, flush=True)
    except Exception:
        pass
    try:
        _scheduler_logs.append(msg)
    except Exception:
        pass

def _scheduler_defaults():
    return {"enabled": True, "interval_sec": 60, "jitter_sec": 3, "last_run_ts": None}

def _validate_state(st: dict) -> dict:
    d = _scheduler_defaults()
    if not isinstance(st, dict):
        return d
    out = {}
    out["enabled"] = bool(st.get("enabled", d["enabled"]))
    # clamp interval 15..43200
    try:
        iv = int(st.get("interval_sec", d["interval_sec"]))
    except Exception:
        iv = d["interval_sec"]
    iv = max(15, min(43200, iv))
    out["interval_sec"] = iv
    # clamp jitter 1..5
    try:
        jv = int(st.get("jitter_sec", d["jitter_sec"]))
    except Exception:
        jv = d["jitter_sec"]
    jv = max(1, min(5, jv))
    out["jitter_sec"] = jv
    out["last_run_ts"] = st.get("last_run_ts")
    return out

def _load_scheduler_state() -> dict:
    try:
        with open(SCHEDULER_STATE_FILE, "r", encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        st = _scheduler_defaults()
        _save_scheduler_state(st)
    except Exception:
        st = _scheduler_defaults()
    return _validate_state(st)

def _save_scheduler_state(st: dict) -> None:
    st = _validate_state(st or {})
    tmp = SCHEDULER_STATE_FILE + ".tmp"
    os.makedirs(os.path.dirname(SCHEDULER_STATE_FILE), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":" ))
    os.replace(tmp, SCHEDULER_STATE_FILE)

def _scheduler_tail_lines(n: int) -> str:
    try:
        n = max(1, int(n))
    except Exception:
        n = 100
    items = list(_scheduler_logs)[-n:]
    return "\n".join(items)

async def _scheduler_loop():
    import asyncio, random, time
    global _scheduler_task
    _sched_log("loop start")
    while True:
        st = _load_scheduler_state()
        if not st.get("enabled"):
            _sched_log("disabled; sleeping 5s")
            await asyncio.sleep(5.0)
            continue
        interval = int(st.get("interval_sec", 60))
        jitter = int(st.get("jitter_sec", 3))
        # do the work
        try:
            cnt = await collect_all_with_micro_jitter()
            ts = int(time.time())
            st["last_run_ts"] = ts
            _save_scheduler_state(st)
            _sched_log(f"run ok: updated={cnt} ts={ts}")
        except Exception as e:
            _sched_log(f"run error: {e.__class__.__name__}: {e}")
        # sleep with jitter
        try:
            delay = max(0.0, float(interval) + random.uniform(-float(jitter), float(jitter)))
        except Exception:
            delay = float(interval)
        await asyncio.sleep(delay)

# public api
async def start_collector():
    """Start background scheduler if not running."""
    import asyncio
    global _scheduler_task
    st = _load_scheduler_state()
    if _scheduler_task and not _scheduler_task.done():
        _sched_log("already running")
        return None
    if not st.get("enabled", True):
        _sched_log("not starting; disabled in state")
        return None
    _scheduler_task = asyncio.create_task(_scheduler_loop(), name="metrics_scheduler")
    _sched_log("started")
    return None

async def stop_collector():
    """Stop background scheduler if running."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _sched_log("cancel requested")
        try:
            await _scheduler_task
        except Exception:
            pass
    _scheduler_task = None
    _sched_log("stopped")
    return None

# config helpers for app commands
def scheduler_get_state() -> dict:
    return _load_scheduler_state()

def scheduler_set_enabled(enabled: bool) -> dict:
    st = _load_scheduler_state()
    st["enabled"] = bool(enabled)
    _save_scheduler_state(st)
    return st

def scheduler_set_timing(interval_sec: int, jitter_sec: int | None = None) -> dict:
    st = _load_scheduler_state()
    st["interval_sec"] = int(interval_sec)
    if jitter_sec is not None:
        st["jitter_sec"] = int(jitter_sec)
    _save_scheduler_state(st)
    return _load_scheduler_state()

def scheduler_tail(n: int) -> str:
    return _scheduler_tail_lines(n)



# public api proxies for app
start_collector = start_collector
stop_collector = stop_collector
scheduler_get_state = scheduler_get_state
scheduler_set_enabled = scheduler_set_enabled
scheduler_set_timing = scheduler_set_timing
scheduler_tail = scheduler_tail
