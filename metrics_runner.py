
import os, json, asyncio, random, time
from datetime import datetime, timezone
from typing import List, Tuple, Dict
from grid_limits import compute_grid_levels
from auto_flags import compute_all_flags

import httpx

STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

# ---------- basic io ----------

def load_pairs(storage_dir: str = STORAGE_DIR) -> List[str]:
    """
    Load trade pairs from JSON. Prefer "<storage_dir>/pairs.json",
    fallback to "<storage_dir>/data/pairs.json". Return only *quote*-USDC/USDT/BUSD/FDUSD pairs,
    uppercase, unique, preserving order.
    """
    path = None
    for candidate in (
        os.path.join(storage_dir, "pairs.json"),
        os.path.join(storage_dir, "data", "pairs.json"),
    ):
        if os.path.exists(candidate):
            path = candidate
            break
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or []
    except Exception:
        return []
    out, seen = [], set()
    for x in data:
        s = str(x).strip().upper()
        if not s or s in seen:
            continue
        if s.endswith(("USDC","USDT","BUSD","FDUSD")):
            seen.add(s)
            out.append(s)
    return out
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

# ---------- binance fetch ----------
BASE = "https://api.binance.com"
_exchange_cache: Dict[str, Tuple[dict, float]] = {}

async def _fetch_price_and_filters(symbol: str) -> Tuple[float|None, dict]:
    async with httpx.AsyncClient(timeout=12.0) as client:
        price = None
        # price (with small retry)
        for attempt in range(3):
            try:
                r = await client.get(f"{BASE}/api/v3/ticker/price", params={"symbol": symbol})
                if r.status_code == 200:
                    price = float((r.json() or {}).get("price"))
                    break
            except Exception:
                pass
            await asyncio.sleep(0.4*(attempt+1))

        # filters (cache 6h)
        filters = {}
        now = time.time()
        cached = _exchange_cache.get(symbol)
        if cached and cached[1] > now:
            filters = dict(cached[0])
        else:
            try:
                r2 = await client.get(f"{BASE}/api/v3/exchangeInfo", params={"symbol": symbol})
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
                        _exchange_cache[symbol] = (dict(filters), now + 6*3600)
            except Exception:
                pass
        return price, filters

# klines + TA
_INTERVALS = {"12h":"12h","6h":"6h","4h":"4h","2h":"2h"}

async def _fetch_klines(symbol: str, interval: str, limit: int = 200):
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{BASE}/api/v3/klines", params={"symbol": symbol, "interval": interval, "limit": limit})
        if r.status_code != 200:
            return []
        data = r.json() or []
        out = []
        for it in data:
            try:
                o = float(it[1]); h = float(it[2]); l = float(it[3]); c = float(it[4]); ct = int(it[6])
                out.append((o,h,l,c,ct))
            except Exception:
                continue
        return out

def _sma_tail(values, n):
    if len(values) < n:
        return None, []
    last = sum(values[-n:]) / n
    tail = []
    if len(values) >= n+1:
        prev = sum(values[-(n+1):-1]) / n
        tail = [prev, last]
    return last, tail

def _atr14(highs, lows, closes):
    if len(closes) < 15:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    if len(trs) < 14:
        return None
    return sum(trs[-14:]) / 14.0

# ---------- market mode ----------
def _signal_for_tf(block: dict) -> str:
    try:
        ma30 = float(block.get("MA30") or 0)
        ma90 = float(block.get("MA90") or 0)
        atr  = float(block.get("ATR14") or 0)
        ma30_arr = list(block.get("MA30_arr") or [])
        ma90_arr = list(block.get("MA90_arr") or [])
    except Exception:
        return "RANGE"
    if atr <= 0:
        return "RANGE"
    d_now = ma30 - ma90
    if len(ma30_arr) >= 2 and len(ma90_arr) >= 2:
        d_prev = float(ma30_arr[-2]) - float(ma90_arr[-2])
    else:
        d_prev = 0.0
    H = 0.4 * atr
    S = 0.1 * atr
    if d_now > +H and (d_now - d_prev) >= +S:
        return "UP"
    if d_now < -H and (d_now - d_prev) <= -S:
        return "DOWN"
    return "RANGE"

def _compute_market_mode(tf_dict: dict, trade_mode: str) -> Tuple[str, Dict[str,str]]:
    signals = {}
    for tf in ("12h","6h","4h","2h"):
        signals[tf] = _signal_for_tf(tf_dict.get(tf) or {})
    md = (trade_mode or "SHORT").upper()
    overall = "RANGE"
    if md == "LONG":
        if signals.get("12h") == "UP" and signals.get("6h") == "UP":
            overall = "UP"
        elif signals.get("12h") == "DOWN" or signals.get("6h") == "DOWN":
            overall = "DOWN"
        else:
            overall = "RANGE"
    else:
        if signals.get("4h") == "DOWN" or signals.get("2h") == "DOWN":
            overall = "DOWN"
        elif signals.get("4h") == "UP" and signals.get("2h") == "UP":
            overall = "UP"
        else:
            overall = "RANGE"
    return overall, signals

# ---------- main collect ----------
async def _collect_one_stub(symbol: str):
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = _coin_file(symbol)
    existing = _read_json(path)
    data = _ensure_skeleton(symbol, now_iso, existing)

    # price + filters
    try:
        price, filters = await _fetch_price_and_filters(symbol)
        if price is not None:
            data["price"] = price
            for tf in ("12h","6h","4h","2h"):
                data["tf"][tf]["close_last"] = price
                data["tf"][tf]["bar_time_utc"] = now_iso
        if filters:
            dst = data.get("filters") or {}
            dst.update(filters)
            data["filters"] = dst
    except Exception as e:
        data["last_error"] = f"PRICE:{e.__class__.__name__}"

    # TA per TF
    tf = data.get("tf") or {}
    for tf_name, interval in _INTERVALS.items():
        try:
            kl = await _fetch_klines(symbol, interval, limit=200)
            if not kl:
                continue
            closes = [k[3] for k in kl]
            highs  = [k[1] for k in kl]
            lows   = [k[2] for k in kl]
            bar_ct = kl[-1][4]
            ma30, ma30_arr = _sma_tail(closes, 30)
            ma90, ma90_arr = _sma_tail(closes, 90)
            atr14 = _atr14(highs, lows, closes)
            block = tf.setdefault(tf_name, {})
            if ma30 is not None: block["MA30"] = ma30
            if ma90 is not None: block["MA90"] = ma90
            block["MA30_arr"] = ma30_arr
            block["MA90_arr"] = ma90_arr
            if atr14 is not None:
                block["ATR14"] = atr14
                last_close = closes[-1]
                if last_close > 0:
                    block["ATR14_pct"] = atr14 / last_close
            block["bar_time_utc"] = datetime.utcfromtimestamp(bar_ct/1000).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as e:
            # keep going for other TFs
            pass
    data["tf"] = tf

    # market mode
    try:
        overall, signals = _compute_market_mode(tf, str(data.get("trade_mode") or "SHORT"))
        data["market_mode"] = overall
        data["signals"] = signals
    except Exception as e:
        data["last_error"] = f"MM:{e.__class__.__name__}"

    
    # OCO for LONG
    try:
        from oco_calc import compute_oco_sell
        if (data.get("trade_mode") or "").upper() == "LONG":
            oco = compute_oco_sell(data)
            if oco:
                data["oco"] = oco
            # grid levels for LONG
            try:
                grid = compute_grid_levels(data)
                if grid:
                    data["grid"] = grid
            except Exception:
                data.pop("grid", None)
            # auto flags (OCO and L0-L3)
            try:
                data["flags"] = compute_all_flags(data)
            except Exception:
                data.pop("flags", None)
        else:
            data.pop("oco", None)
    except Exception as e:
        data["last_error"] = f"OCO:{e.__class__.__name__}"
    else:
        data["last_success_utc"] = now_iso
        data.pop("last_error", None)
    _write_json_atomic(path, data)

# public entry for /now (no jitter)

# public entry for /now (micro jitter to avoid burst hitting Binance)

async def collect_all_with_micro_jitter(min_ms: int = 120, max_ms: int = 360, **kwargs) -> int:
    # compatibility: allow scheduler to pass jitter_max_sec (seconds)
    jsec = kwargs.get("jitter_max_sec")
    if isinstance(jsec, (int, float)) and jsec > 0:
        try:
            min_ms = max(50, int(min_ms))
            max_ms = max(min_ms + 50, int(min_ms + jsec * 1000))
        except Exception:
            pass
    pairs = load_pairs()
    if not pairs:
        return 0
    n = 0
    for i, sym in enumerate(pairs):
        await _collect_one_stub(sym)
        n += 1
        # micro sleep between symbols to avoid hammering remote APIs
        try:
            await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000.0)
        except Exception:
            # do not fail the whole /now if sleep fails
            pass
    return n
async def collect_all_no_jitter() -> int:
    pairs = load_pairs()
    if not pairs:
        return 0
    n = 0
    for sym in pairs:
        await _collect_one_stub(sym)
        n += 1
    return n


# --- compatibility stubs (no background collector) ---
async def start_collector():
    return None

async def stop_collector():
    return None
