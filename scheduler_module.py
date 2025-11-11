
import os, asyncio, json, logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from aiogram import Router, types
from aiogram.filters import Command, CommandObject

from market_module import evaluate_for_symbol, append_raw
from metrics_module import run_now_for_symbol

router = Router()
log = logging.getLogger("scheduler")

DEFAULT_CFG = {
    "enabled": True,
    "period_sec": 300,
    "jitter_sec": 2,
    "publish_hours": 12,
    "last_tick_utc": None,
    "next_due_utc": None,
    "last_publish_utc": None,
    "next_publish_utc": None
}

def storage_dir() -> Path:
    d = Path(os.getenv("STORAGE_DIR") or "./storage")
    d.mkdir(parents=True, exist_ok=True)
    return d

def cfg_path() -> Path: return storage_dir() / "scheduler.json"
def coins_path() -> Path: return storage_dir() / "coins.txt"
def log_path() -> Path: return storage_dir() / "scheduler_log.jsonl"

def load_cfg() -> Dict[str, Any]:
    p = cfg_path()
    if not p.exists(): save_cfg(DEFAULT_CFG.copy())
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception: return DEFAULT_CFG.copy()

def save_cfg(cfg: Dict[str, Any]) -> None:
    cfg = {**DEFAULT_CFG, **cfg}
    cfg_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def utcnow() -> datetime: return datetime.now(timezone.utc)

def coins_list() -> List[str]:
    p = coins_path()
    if not p.exists(): return []
    try: return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception: return []

def jitter_sleep(period: int, jitter: int) -> float:
    if jitter <= 0: return float(period)
    import random
    return max(1.0, period + random.uniform(-jitter, jitter))

def publish_due(now: datetime, cfg: Dict[str, Any]) -> bool:
    ph = int(cfg.get("publish_hours") or 12)
    last_pub = cfg.get("last_publish_utc")
    if not last_pub: return True
    try: last_dt = datetime.fromisoformat(last_pub)
    except Exception: return True
    return now - last_dt >= timedelta(hours=ph)

def append_scheduler_log(event: Dict[str, Any]) -> None:
    event = {**event, "ts": utcnow().isoformat()}
    with log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

async def publish_symbol(symbol: str, publish_hours: int):
    d = storage_dir()
    overall_now, tf_signals_now = evaluate_for_symbol(str(d), symbol)
    fp = d / f"mode_raw_{symbol}.jsonl"
    cutoff = utcnow() - timedelta(hours=publish_hours)
    up = down = rng = total = 0
    if fp.exists():
        for line in fp.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(line)
                ts = obj.get("ts")
                dt = datetime.fromisoformat(ts) if ts else None
                if dt and dt < cutoff: continue
                total += 1
                m = (obj.get("overall") or "").upper()
                if m == "UP": up += 1
                elif m == "DOWN": down += 1
                else: rng += 1
            except Exception:
                continue
    mode = "RANGE"
    if total > 0:
        if up/total > 0.6: mode = "UP"
        elif down/total > 0.6: mode = "DOWN"
    sp = d / f"{symbol}.json"
    data = {}
    if sp.exists():
        try: data = json.loads(sp.read_text(encoding="utf-8"))
        except Exception: data = {}
    data["market_mode"] = mode
    data["signals"] = tf_signals_now
    data["mode_updated_utc"] = utcnow().isoformat()
    sp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"component=publish action=apply symbol={symbol} mode={mode} total={total}")
    return mode, tf_signals_now

async def tick_once() -> None:
    cfg = load_cfg()
    now = utcnow()
    append_scheduler_log({"event":"tick_start"})
    log.info(f"component=scheduler action=tick_start enabled={cfg.get('enabled')} period_sec={cfg.get('period_sec')} jitter_sec={cfg.get('jitter_sec')} publish_hours={cfg.get('publish_hours')}")
    syms = coins_list()
    for sym in syms:
        try:
            await run_now_for_symbol(sym, str(storage_dir()))
            log.info(f"component=metrics action=collected symbol={sym}")
            overall, tf_signals = evaluate_for_symbol(str(storage_dir()), sym)
            append_raw(str(storage_dir()), sym, overall, tf_signals)
            log.info(f"component=market_mode action=raw_append symbol={sym} overall={overall}")
            if publish_due(now, cfg):
                await publish_symbol(sym, int(cfg.get('publish_hours') or 12))
        except Exception:
            log.warning(f"component=symbol action=fail symbol={sym}", exc_info=True)
    cfg["last_tick_utc"] = now.isoformat()
    if publish_due(now, cfg):
        cfg["last_publish_utc"] = now.isoformat()
        cfg["next_publish_utc"] = (now + timedelta(hours=int(cfg.get('publish_hours') or 12))).isoformat()
    period = int(cfg.get("period_sec") or 300)
    jitter = int(cfg.get("jitter_sec") or 0)
    sleep_sec = jitter_sleep(period, jitter)
    cfg["next_due_utc"] = (now + timedelta(seconds=sleep_sec)).isoformat()
    save_cfg(cfg)
    append_scheduler_log({"event":"tick_done", "sleep_sec": sleep_sec})

async def run_scheduler_loop(stop_event: asyncio.Event) -> None:
    log.info("component=scheduler action=loop_start")
    while not stop_event.is_set():
        cfg = load_cfg()
        if not cfg.get("enabled", True):
            await asyncio.sleep(max(1, int(cfg.get("period_sec") or 60)))
            continue
        try:
            await tick_once()
        except Exception:
            log.exception("component=scheduler action=tick_exception")
        cfg = load_cfg()
        nd = cfg.get("next_due_utc")
        if nd:
            try:
                next_dt = datetime.fromisoformat(nd)
                delta = (next_dt - utcnow()).total_seconds()
                await asyncio.sleep(max(1.0, delta))
                continue
            except Exception:
                pass
        await asyncio.sleep(max(1, int(cfg.get("period_sec") or 60)))

def human_cfg() -> str:
    cfg = load_cfg()
    parts = [
        f"enabled: {cfg.get('enabled')}",
        f"period_sec: {cfg.get('period_sec')}",
        f"jitter_sec: {cfg.get('jitter_sec')}",
        f"publish_hours: {cfg.get('publish_hours')}",
        f"last_tick_utc: {cfg.get('last_tick_utc')}",
        f"next_due_utc: {cfg.get('next_due_utc')}",
        f"last_publish_utc: {cfg.get('last_publish_utc')}",
        f"next_publish_utc: {cfg.get('next_publish_utc')}",
    ]
    return "\n".join(parts)

from utils import mono

@router.message(Command("scheduler"))
async def cmd_scheduler(msg: types.Message, command: CommandObject):
    args = ((command.args or "").strip() if command else "")
    if not args: return await msg.answer(mono(human_cfg()))
    parts = args.split()
    cfg = load_cfg()
    try:
        if parts[0].lower() in ("on", "off"):
            cfg["enabled"] = (parts[0].lower() == "on")
        elif parts[0].lower() == "period" and len(parts) >= 2:
            cfg["period_sec"] = max(5, int(parts[1]))
        elif parts[0].lower() == "jitter" and len(parts) >= 2:
            j = int(parts[1]); cfg["jitter_sec"] = max(0, min(3, j))
        elif parts[0].lower() == "publish" and len(parts) >= 2:
            cfg["publish_hours"] = max(1, int(parts[1]))
        else:
            return
        period = int(cfg.get("period_sec") or 300)
        jitter = int(cfg.get("jitter_sec") or 0)
        import random
        sleep_sec = max(1.0, period + random.uniform(-jitter, jitter) if jitter>0 else period)
        cfg["next_due_utc"] = (datetime.now(timezone.utc) + timedelta(seconds=sleep_sec)).isoformat()
        save_cfg(cfg)
        return await msg.answer(mono(human_cfg()))
    except Exception:
        return
