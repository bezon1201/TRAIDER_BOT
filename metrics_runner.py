
import os, json, asyncio, random
from datetime import datetime, timezone
from typing import List

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

_task = None

def load_pairs(storage_dir: str = STORAGE_DIR) -> List[str]:
    path = os.path.join(storage_dir, "pairs.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            res=[]; seen=set()
            for x in data:
                s = str(x).strip().upper()
                if s and s not in seen:
                    seen.add(s); res.append(s)
            return res
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return []

def _coin_file(symbol: str, storage_dir: str = STORAGE_DIR) -> str:
    os.makedirs(storage_dir, exist_ok=True)
    return os.path.join(storage_dir, f"{symbol}.json")

def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def _write_json_atomic(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":" ))
    os.replace(tmp, path)

def _ensure_skeleton(symbol: str, now_iso: str, existing: dict) -> dict:
    out = dict(existing) if isinstance(existing, dict) else {}
    out.setdefault("symbol", symbol)
    out.setdefault("trade_mode", out.get("trade_mode") or "SHORT")
    out.setdefault("price", out.get("price"))
    out.setdefault("filters", out.get("filters") or {})
    tf = out.setdefault("tf", {})
    for key in ("12h","6h","4h","2h"):
        block = tf.setdefault(key, {})
        block.setdefault("close_last", block.get("close_last"))
        block.setdefault("MA30", block.get("MA30"))
        block.setdefault("MA90", block.get("MA90"))
        block.setdefault("MA30_arr", block.get("MA30_arr") or [])
        block.setdefault("MA90_arr", block.get("MA90_arr") or [])
        block.setdefault("ATR14", block.get("ATR14"))
        block.setdefault("ATR14_pct", block.get("ATR14_pct"))
        block["collected_at_utc"] = now_iso
    out["last_update_utc"] = now_iso
    return out

async def _collect_one_stub(symbol: str):
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = _coin_file(symbol)
    existing = _read_json(path)
    data = _ensure_skeleton(symbol, now_iso, existing)
    _write_json_atomic(path, data)

async def _loop():
    # staggered start
    await asyncio.sleep(random.uniform(0.5, 1.5))
    while True:
        try:
            pairs = load_pairs()
            interval = int(os.getenv("COLLECT_INTERVAL_SEC", "600") or "600")
            if interval < 60: interval = 60
            for sym in pairs:
                await _collect_one_stub(sym)
                await asyncio.sleep(interval * random.uniform(0.05, 0.10))
        except Exception:
            pass
        finally:
            await asyncio.sleep(int(os.getenv("COLLECT_INTERVAL_SEC", "600") or "600") * 0.85)

async def start_collector():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())

async def stop_collector():
    global _task
    if _task:
        _task.cancel()
        _task = None


async def collect_one_now(symbol: str) -> None:
    await _collect_one_stub(symbol)

async def collect_all_now() -> int:
    pairs = load_pairs()
    n = 0
    for sym in pairs:
        await _collect_one_stub(sym)
        n += 1
    return n
