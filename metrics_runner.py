
import os, json, asyncio, random
from datetime import datetime, timezone
import httpx

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


async def _fetch_price_and_filters(symbol: str):
    base = "https://api.binance.com"
    async with httpx.AsyncClient(timeout=8.0) as client:
        price = None
        try:
            r = await client.get(f"{base}/api/v3/ticker/price", params={"symbol": symbol})
            if r.status_code == 200:
                price = float((r.json() or {}).get("price"))
        except Exception:
            price = None
        filters = {}
        try:
            r2 = await client.get(f"{base}/api/v3/exchangeInfo", params={"symbol": symbol})
            if r2.status_code == 200:
                j = r2.json() or {}
                syms = (j.get("symbols") or [])
                if syms:
                    fs = syms[0].get("filters") or []
                    for f in fs:
                        if f.get("filterType") == "PRICE_FILTER":
                            filters["tickSize"] = f.get("tickSize")
                        if f.get("filterType") == "LOT_SIZE":
                            filters["stepSize"] = f.get("stepSize")
        except Exception:
            pass
        return price, filters

async def _collect_one_stub(symbol: str):
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = _coin_file(symbol)
    existing = _read_json(path)
    data = _ensure_skeleton(symbol, now_iso, existing)
    # fetch price & filters
    price, filters = await _fetch_price_and_filters(symbol)
    if price is not None:
        data["price"] = price
        # put close_last for all TFs (temporary until full MA/ATR calc)
        for tf in ("12h","6h","4h","2h"):
            try:
                data["tf"][tf]["close_last"] = price
                data["tf"][tf]["bar_time_utc"] = now_iso
            except Exception:
                pass
    if filters:
        data["filters"] = filters
    _write_json_atomic(path, data)

async def _loop():
    # staggered start
    await asyncio.sleep(random.uniform(0.5, 1.5))
    while True:
        try:
            await collect_all_with_jitter()
        except Exception:
            pass
        finally:
            # main sleep to next cycle (≈ 85% of interval)
            await asyncio.sleep(int(os.getenv("COLLECT_INTERVAL_SEC", "600") or "600") * 0.85)

async def start_collector():
):
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


async def collect_all_with_jitter(interval_sec: int | None = None) -> int:
    \"\"\"
    Collect all symbols from pairs.json with 5–10% jitter between coins.
    If interval_sec is None, read COLLECT_INTERVAL_SEC (default 600) for jitter base.
    Returns number of updated symbols.
    \"\"\"
    pairs = load_pairs()
    if not pairs:
        return 0
    base = interval_sec if isinstance(interval_sec, int) and interval_sec > 0 else int(os.getenv("COLLECT_INTERVAL_SEC", "600") or "600")
    if base < 60: base = 60
    n = 0
    for sym in pairs:
        await _collect_one_stub(sym)
        n += 1
        # 5–10% jitter pause between coins
        await asyncio.sleep(base * random.uniform(0.05, 0.10))
    return n
