
import os, json, asyncio, time, inspect
from datetime import datetime, timezone
from typing import Optional

from budget_long import init_if_needed, load_state as _load_budget_state, weekly_tick, month_end_tick
from metrics_runner import collect_all_with_micro_jitter

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")
STATE_PATH = os.path.join(STORAGE_DIR, "scheduler_state.json")

_task: Optional[asyncio.Task] = None
_lock = asyncio.Lock()

DEFAULT_STATE = {
    "enabled": False,
    "interval_sec": 60,
    "jitter_max_sec": 3,
    "last_run_utc": None,
    "last_ok_utc": None,
    "last_error": None,
    "updated_last": 0,
}

def _read_json(path: str, default: dict) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return json.loads(json.dumps(default))

def _write_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _parse_iso(s: str) -> float:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0

def _log(*args):
    try:
        print("[sched]", *args, flush=True)
    except Exception:
        pass

def _load_state() -> dict:
    st = _read_json(STATE_PATH, DEFAULT_STATE)
    st.setdefault("enabled", False)
    st.setdefault("interval_sec", 60)
    st.setdefault("jitter_max_sec", 3)
    return st

def _save_state(st: dict):
    _write_json(STATE_PATH, st)

async def _tick() -> int:
    init_if_needed(STORAGE_DIR)
    bstate = _load_budget_state(STORAGE_DIR)
    now = time.time()

    # Weekly
    try:
        nxt_week = bstate.get("next_week_start_utc")
        if nxt_week:
            if now >= _parse_iso(nxt_week) - 1:
                _log("weekly_tick due → running")
                weekly_tick(STORAGE_DIR)
    except Exception as e:
        _log("weekly_tick error:", e)

    # Monthly
    try:
        month_end = bstate.get("month_end_utc")
        if month_end:
            if now >= _parse_iso(month_end) - 1:
                _log("month_end_tick due → running")
                month_end_tick(STORAGE_DIR)
    except Exception as e:
        _log("month_end_tick error:", e)

    # Refresh pairs (no posts)
    st = _load_state()
    jitter = int(st.get("jitter_max_sec", 3) or 0)
    try:
        sig = inspect.signature(collect_all_with_micro_jitter)
        if "jitter_max_sec" in sig.parameters:
            n = await collect_all_with_micro_jitter(jitter_max_sec=jitter)
        else:
            n = await collect_all_with_micro_jitter()
        return int(n or 0)
    except Exception as e:
        _log("collect error:", e)
        return 0

async def _loop():
    global _task
    while True:
        st = _load_state()
        if not st.get("enabled", False):
            _log("disabled; sleeping 5s")
            await asyncio.sleep(5)
            continue
        interval = max(10, int(st.get("interval_sec", 60) or 60))
        st["last_run_utc"] = _iso(time.time()); _save_state(st)
        updated = await _tick()
        st["updated_last"] = int(updated); st["last_ok_utc"] = _iso(time.time()); st["last_error"] = None
        _save_state(st)
        _log(f"tick done updated={updated} next_in={interval}s")
        await asyncio.sleep(interval)

async def ensure_scheduler_started():
    global _task
    async with _lock:
        if _task and not _task.done():
            return
        _task = asyncio.create_task(_loop())
        _log("background task started")

async def sched_on():
    st = _load_state(); st["enabled"] = True; _save_state(st)
    await ensure_scheduler_started()
    return await sched_status()

async def sched_off():
    st = _load_state(); st["enabled"] = False; _save_state(st)
    return await sched_status()

async def sched_run_once():
    updated = await _tick()
    st = _load_state()
    st["updated_last"] = int(updated); st["last_ok_utc"] = _iso(time.time()); st["last_run_utc"] = _iso(time.time())
    _save_state(st)
    return f"```\nScheduler run: updated={updated}\n```"

async def sched_status():
    st = _load_state()
    lines = [
        f"enabled: {st.get('enabled', False)}",
        f"interval: {int(st.get('interval_sec', 60) or 60)}s",
        f"jitter:   {int(st.get('jitter_max_sec', 3) or 0)}s",
        f"last_run: {st.get('last_run_utc') or '—'}",
        f"last_ok:  {st.get('last_ok_utc') or '—'}",
        f"updated_last: {int(st.get('updated_last', 0) or 0)}",
    ]
    return "```\n" + "\n".join(lines) + "\n```"

async def sched_set(interval: int = None, jitter: int = None):
    st = _load_state()
    if interval is not None:
        st["interval_sec"] = max(10, int(interval))
    if jitter is not None:
        st["jitter_max_sec"] = max(0, int(jitter))
    _save_state(st)
    return await sched_status()
