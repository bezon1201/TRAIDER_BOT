
import os
from typing import Dict, List
from datetime import datetime, timezone

import httpx
import asyncio

BINANCE_BASE = "https://api.binance.com"

def normalize_symbol(token: str) -> str:
    return "".join(ch for ch in (token or "").lower() if ch.isalnum())

def to_binance_symbol(symbol_lc: str) -> str:
    return (symbol_lc or "").upper()

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

async def fetch_klines(symbol: str, interval: str, limit: int = 200) -> List[List]:
    url = f"{BINANCE_BASE}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()

async def fetch_price(symbol: str) -> float:
    url = f"{BINANCE_BASE}/api/v3/ticker/price"
    params = {"symbol": symbol}
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        return float(data.get("price"))

async def fetch_filters(symbol: str) -> Dict[str, str]:
    url = f"{BINANCE_BASE}/api/v3/exchangeInfo"
    params = {"symbol": symbol}
    timeout = httpx.Timeout(15.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    try:
        sym_info = (data.get("symbols") or [])[0]
        filters = sym_info.get("filters") or []
        out: Dict[str,str] = {}
        for f in filters:
            ftype = f.get("filterType")
            if ftype == "PRICE_FILTER":
                out["tickSize"] = f.get("tickSize")
            elif ftype == "LOT_SIZE":
                out["stepSize"] = f.get("stepSize")
                out["minQty"]   = f.get("minQty")
            elif ftype == "MIN_NOTIONAL":
                out["minNotional"] = f.get("minNotional")
        return out
    except Exception:
        return {}

def sma(values: List[float], length: int) -> List[float]:
    out: List[float] = []
    if length <= 0:
        return out
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= length:
            s -= values[i - length]
        if i >= length - 1:
            out.append(s / length)
    return out

def atr14(klines: List[List]) -> float:
    if len(klines) < 15:
        return 0.0
    trs: List[float] = []
    prev_close = float(klines[-15][4])
    for k in klines[-14:]:
        high = float(k[2])
        low  = float(k[3])
        close_prev = prev_close
        tr = max(high - low, abs(high - close_prev), abs(low - close_prev))
        trs.append(tr)
        prev_close = float(k[4])
    return sum(trs) / 14.0

def build_tf_block(klines: List[List]) -> Dict:
    closes = [float(k[4]) for k in klines]
    ma30_series = sma(closes, 30)
    ma90_series = sma(closes, 90)
    ma30 = ma30_series[-1] if len(ma30_series) else 0.0
    ma90 = ma90_series[-1] if len(ma90_series) else 0.0
    ma30_arr = ma30_series[-2:] if len(ma30_series) >= 2 else ma30_series[-1:]
    ma90_arr = ma90_series[-2:] if len(ma90_series) >= 2 else ma90_series[-1:]
    atr = atr14(klines)
    close_last = float(klines[-1][4])
    atr_pct = (atr / close_last) if close_last > 0 else 0.0
    bar_close_ms = int(klines[-1][6])
    bar_time_utc = datetime.fromtimestamp(bar_close_ms/1000, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
    return {
        "close_last": close_last,
        "MA30": ma30,
        "MA90": ma90,
        "MA30_arr": ma30_arr,
        "MA90_arr": ma90_arr,
        "ATR14": atr,
        "ATR14_pct": atr_pct,
        "collected_at_utc": now_utc_iso(),
        "bar_time_utc": bar_time_utc,
    }

async def collect_symbol_metrics(symbol_lc: str) -> Dict:
    sym = to_binance_symbol(symbol_lc)
    intervals = ["2h", "4h", "6h", "12h"]
    tf: Dict[str, Dict] = {}
    for itv in intervals:
        ks = await fetch_klines(sym, itv, limit=200)
        tf[itv] = build_tf_block(ks)
    price = await fetch_price(sym)
    filters = await fetch_filters(sym)
    return {
        "symbol": sym,
        "price": price,
        "filters": filters,
        "tf": tf,
        "last_update_utc": now_utc_iso(),
    }

def write_json(storage_dir: str, symbol_lc: str, payload: Dict) -> None:
    os.makedirs(storage_dir, exist_ok=True)
    path = os.path.join(storage_dir, f"{symbol_lc}.json")
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

async def run_now_for_all(symbols: List[str], storage_dir: str) -> None:
    for s in symbols:
        await run_now_for_symbol(s, storage_dir)

async def run_now_for_symbol(symbol_lc: str, storage_dir: str) -> None:
    m = await collect_symbol_metrics(symbol_lc)
    write_json(storage_dir, symbol_lc, m)
