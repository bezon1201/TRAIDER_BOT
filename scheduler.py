
import os, json, asyncio, time
from datetime import datetime, timezone
from typing import Optional

from budget_long import init_if_needed, load_state as _load_budget_state, weekly_tick, month_end_tick, load_settings as _load_budget_settings
from metrics_runner import collect_all_with_micro_jitter

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")
STATE_PATH = os.path.join(STORAGE_DIR, "scheduler_state.json")

_task: Optional[asyncio.Task] = None
_lock = asyncio.Lock()

DEFAULT_STATE = {
    "enabled": False,
    "interval_sec": 60,
    "last_run_utc": None,
    "last_ok_utc": None,
    "last_error": None,
    "last_week_anchor": None,
    "last_month_anchor": None,
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

def _iso(dt: float) -> str:
    return datetime.fromtimestamp(dt, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _parse_iso(s: str) -> float:
    try:
        # naive ISO Z
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0

def _log(*args):
    try:
        msg = " ".join(str(a) for a in args)
        print(f"[sched] {msg}", flush=True)
    except Exception:
        pass

def _load_state() -> dict:
    st = _read_json(STATE_PATH, DEFAULT_STATE)
    # guard types
    st.setdefault("enabled", False)
    st.setdefault("interval_sec", 60)
    return st

def _save_state(st: dict):
    _write_json(STATE_PATH, st)

async def _tick() -> int:
    # weekly/monthly anchors
    init_if_needed(STORAGE_DIR)
    bstate = _load_budget_state(STORAGE_DIR)
    now = time.time()

    # Weekly roll
    try:
        nxt_week = bstate.get("next_week_start_utc")
        if nxt_week:
            nxt_week_ts = _parse_iso(nxt_week)
            if now >= nxt_week_ts - 1:  # small guard
                _log("weekly_tick due → running")
                weekly_tick(STORAGE_DIR)
    except Exception as e:
        _log("weekly_tick error:", e)

    # Monthly roll
    try:
        month_end = bstate.get("month_end_utc")
        if month_end:
            me_ts = _parse_iso(month_end)
            if now >= me_ts - 1:
                _log("month_end_tick due → running")
                month_end_tick(STORAGE_DIR)
    except Exception as e:
        _log("month_end_tick error:", e)

    # Prices/flags refresh (no posts)
    try:
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
    """Start background task if enabled and not running."""
    global _task
    async with _lock:
        if _task and not _task.done():
            return
        # Always create the task; it will idle while disabled
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
        f"interval: {st.get('interval_sec', 60)}s",
        f"last_run: {st.get('last_run_utc') or '—'}",
        f"last_ok:  {st.get('last_ok_utc') or '—'}",
        f"updated_last: {st.get('updated_last', 0)}",
    ]
    return "```\n" + "\n".join(lines) + "\n```"
