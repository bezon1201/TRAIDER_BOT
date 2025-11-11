
import os, re, asyncio, json
from pathlib import Path
from typing import Dict, Tuple
from aiogram import Router, types
from aiogram.filters import Command, CommandObject

router = Router()
SAFE = re.compile(r"^[a-z0-9]+$")
TF_KEYS = ["6h", "12h"]

def storage_dir() -> Path:
    d = Path(os.getenv("STORAGE_DIR") or "./storage")
    d.mkdir(parents=True, exist_ok=True)
    return d

def coins_file() -> Path:
    return storage_dir() / "coins.txt"

def read_coins() -> list[str]:
    f = coins_file()
    if not f.exists(): return []
    return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]

def parse_csv(raw: str) -> list[str]:
    return [x.strip().lower() for x in (raw.split(",") if raw else []) if x.strip()]

def _load_metrics(storage_dir_str: str, symbol: str) -> dict | None:
    p = Path(storage_dir_str) / f"{symbol}.json"
    if not p.exists(): return None
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception: return None

def _signal_for_tf(tf_block: dict) -> str:
    if not isinstance(tf_block, dict): return "RANGE"
    close = tf_block.get("close_last", 0.0) or 0.0
    atr = tf_block.get("ATR14", 0.0) or 0.0
    if (atr or 0.0) <= 0 or (close or 0.0) <= 0: return "RANGE"
    ma30 = tf_block.get("SMA30"); ma90 = tf_block.get("SMA90")
    ma30_arr = tf_block.get("SMA30_arr") or []; ma90_arr = tf_block.get("SMA90_arr") or []
    diff_now = (float(ma30) - float(ma90)) if isinstance(ma30,(int,float)) and isinstance(ma90,(int,float)) else None
    try: diff_prev = float(ma30_arr[-2]) - float(ma90_arr[-2])
    except Exception: diff_prev = diff_now
    H = 0.6 * float(atr)
    if diff_now is None: return "RANGE"
    if diff_prev is None: diff_prev = diff_now
    if diff_now > H and diff_now >= diff_prev: return "UP"
    if diff_now < -H and diff_now <= diff_prev: return "DOWN"
    return "RANGE"

def _overall_from_tf(tf_signals: Dict[str, str]) -> str:
    s6 = tf_signals.get("6h", "RANGE"); s12 = tf_signals.get("12h", "RANGE")
    if s6 == "UP" and s12 == "UP": return "UP"
    if s6 == "DOWN" or s12 == "DOWN": return "DOWN"
    return "RANGE"

def evaluate_for_symbol(storage_dir_str: str, symbol: str) -> Tuple[str, Dict[str, str]]:
    data = _load_metrics(storage_dir_str, symbol) or {}; tf = data.get("tf") or {}
    tf_signals = {k: _signal_for_tf(tf.get(k) or {}) for k in TF_KEYS}
    overall = _overall_from_tf(tf_signals); return overall, tf_signals

def append_raw(storage_dir_str: str, symbol: str, overall: str, tf_signals: Dict[str, str]) -> None:
    d = Path(storage_dir_str); d.mkdir(parents=True, exist_ok=True)
    metrics = _load_metrics(storage_dir_str, symbol) or {}
    ts = None
    for k in reversed(TF_KEYS):
        blk = (metrics.get("tf") or {}).get(k) or {}
        if "bar_time_utc" in blk: ts = blk.get("bar_time_utc"); break
    rec = {"symbol": symbol, "ts": ts, "overall": overall, "tf": tf_signals}
    with (d / f"mode_raw_{symbol}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

@router.message(Command("market"))
async def cmd_market(msg: types.Message, command: CommandObject):
    from utils import mono
    args = (command.args or "").strip() if command else ""
    if not args: return
    parts = args.split(None, 1); sub = parts[0].lower(); rest = parts[1] if len(parts) > 1 else ""
    if sub == "force":
        syms = read_coins() if not rest else parse_csv(rest)
        syms = [s for s in syms if SAFE.fullmatch(s)]
        if not syms: return
        d = storage_dir()
        async def run(s: str):
            try:
                overall, tf_signals = evaluate_for_symbol(str(d), s)
                append_raw(str(d), s, overall, tf_signals)
            except Exception: pass
        await asyncio.gather(*(run(s) for s in syms)); return
    if sub == "publish":
        from scheduler_module import publish_symbol, load_cfg
        try: ph = int(load_cfg().get("publish_hours") or 12)
        except Exception: ph = 12
        syms = read_coins() if not rest else parse_csv(rest)
        syms = [s for s in syms if SAFE.fullmatch(s)]
        if not syms: return await msg.answer(mono("(пусто)"))
        ok = []
        for s in syms:
            try: await publish_symbol(s, ph); ok.append(s)
            except Exception: pass
        return await msg.answer(mono("published: " + ", ".join(ok)))
