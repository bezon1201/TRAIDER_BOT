
import os, json, time, hmac, hashlib, tempfile
from typing import Dict, List, Tuple
import httpx
from metrics_runner import load_pairs

BINANCE_API = "https://api.binance.com"

def _storage_path(storage_dir: str) -> str:
    os.makedirs(storage_dir, exist_ok=True)
    return os.path.join(storage_dir, "portfolio.json")

def _load_state(storage_dir: str) -> Dict:
    path = _storage_path(storage_dir)
    if not os.path.exists(path):
        return {"invested_total": 0.0, "history": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"invested_total": 0.0, "history": []}

def _atomic_write(path: str, data: Dict) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=d, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, separators=(",", ":"))
        tmp = tf.name
    os.replace(tmp, path)

def adjust_invested_total(storage_dir: str, delta: float) -> float:
    state = _load_state(storage_dir)
    state["invested_total"] = round(float(state.get("invested_total", 0.0)) + float(delta), 8)
    state.setdefault("history", []).append({
        "ts": int(time.time() * 1000),
        "delta": float(delta),
        "total": float(state["invested_total"]),
    })
    _atomic_write(_storage_path(storage_dir), state)
    return float(state["invested_total"])

def get_usdc_spot_earn_total(storage_dir: str) -> float:
    state = _load_state(storage_dir)
    try:
        if "usdc_trade_free" in state:
            return float(state.get("usdc_trade_free", 0.0))
        # fallback to legacy total key (spot+earn)
        return float(state.get("usdc_total", 0.0))
    except Exception:
        return 0.0

def _sign(query: str, secret: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

async def _signed_get(client: httpx.AsyncClient, path: str, key: str, secret: str, params: Dict = None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 10_000
    q = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sig = _sign(q, secret)
    url = f"{BINANCE_API}{path}?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": key}
    r = await client.get(url, headers=headers)
    r.raise_for_status()
    return r.json()

async def _public_get(client: httpx.AsyncClient, path: str, params: Dict = None):
    url = f"{BINANCE_API}{path}"
    r = await client.get(url, params=params or {})
    r.raise_for_status()
    return r.json()


def _signed_get_sync(path: str, key: str, secret: str, params: Dict = None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 10_000
    q = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sig = _sign(q, secret)
    url = f"{BINANCE_API}{path}?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": key}
    with httpx.Client(timeout=10.0) as client:
        r = client.get(url, headers=headers, params=None)
        r.raise_for_status()
        return r.json()


def refresh_usdc_trade_free(storage_dir: str) -> float:
    """
    Quietly refresh free USDC available for live trading (spot.free + Earn FLEX)
    and persist it into portfolio.json. Returns the computed amount.
    """
    key = os.getenv("BINANCE_API_KEY", "").strip()
    secret = os.getenv("BINANCE_API_SECRET", "").strip()
    if not key or not secret:
        # fallback to cached state
        return get_usdc_spot_earn_total(storage_dir)

    try:
        # Spot free USDC
        account = _signed_get_sync("/api/v3/account", key, secret, params={})
        spot_free = 0.0
        for b in account.get("balances", []):
            if b.get("asset") == "USDC":
                try:
                    spot_free += float(b.get("free", "0") or 0.0)
                except Exception:
                    pass

        # FLEX Earn USDC (withdrawable)
        flex = _signed_get_sync("/sapi/v1/simple-earn/flexible/position", key, secret, params={"size": 100})
        rows = flex.get("rows") if isinstance(flex, dict) else flex
        if rows is None:
            rows = []
        earn_flex = 0.0
        for p in rows:
            asset = p.get("asset") or p.get("assetSymbol") or p.get("assetName")
            if asset == "USDC":
                try:
                    total = float(p.get("totalAmount") or p.get("total") or p.get("amount", 0) or 0)
                    earn_flex += total
                except Exception:
                    pass

        trade_free = spot_free + earn_flex

        state = _load_state(storage_dir)
        state["usdc_spot_free"] = round(spot_free, 8)
        state["usdc_earn_flex"] = round(earn_flex, 8)
        state["usdc_trade_free"] = round(trade_free, 8)
        _atomic_write(_storage_path(storage_dir), state)
        return trade_free
    except Exception:
        # fallback to cached value if network or API fails
        return get_usdc_spot_earn_total(storage_dir)

async def _load_spot_balances(client: httpx.AsyncClient, key: str, secret: str) -> Dict[str, float]:
    data = await _signed_get(client, "/api/v3/account", key, secret, params={})
    balances = {}
    for b in data.get("balances", []):
        free = float(b.get("free", "0") or 0)
        locked = float(b.get("locked", "0") or 0)
        amt = free + locked
        if amt > 0:
            balances[b["asset"]] = balances.get(b["asset"], 0.0) + amt
    return balances

async def _load_earn_positions(client: httpx.AsyncClient, key: str, secret: str) -> Dict[str, float]:
    out = {}
    try:
        flex = await _signed_get(client, "/sapi/v1/simple-earn/flexible/position", key, secret, params={"size": 100})
        rows = flex.get("rows", flex if isinstance(flex, list) else [])
        for p in rows:
            asset = p.get("asset") or p.get("assetSymbol") or p.get("assetName")
            total = float(p.get("totalAmount") or p.get("total") or p.get("amount", 0) or 0)
            if asset and total > 0:
                out[asset] = out.get(asset, 0.0) + total
    except Exception:
        pass
    try:
        locked = await _signed_get(client, "/sapi/v1/simple-earn/locked/position", key, secret, params={"size": 100})
        rows = locked.get("rows", locked if isinstance(locked, list) else [])
        for p in rows:
            asset = p.get("asset") or p.get("assetSymbol") or p.get("assetName")
            total = float(p.get("totalAmount") or p.get("purchasedAmount") or 0)
            if asset and total > 0:
                out[asset] = out.get(asset, 0.0) + total
    except Exception:
        pass
    return out

async def _get_usd_prices(client: httpx.AsyncClient, assets: List[str]) -> Dict[str, float]:
    prices = {}
    stables = {"USDT", "USDC", "BUSD", "FDUSD"}
    for a in assets:
        if a in stables:
            prices[a] = 1.0
    symbols = [f"{a}USDT" for a in assets if a not in stables]
    if symbols:
        try:
            data = await _public_get(client, "/api/v3/ticker/price", params={"symbols": str(symbols).replace("'", '"')})
            for item in data:
                sym = item.get("symbol","")
                if sym.endswith("USDT"):
                    asset = sym[:-4]
                    prices[asset] = float(item["price"])
        except Exception:
            for sym in symbols:
                try:
                    d = await _public_get(client, "/api/v3/ticker/price", params={"symbol": sym})
                    prices[sym[:-4]] = float(d["price"])
                except Exception:
                    pass
    return prices

def _format_block(title: str, rows: List[Tuple[str, float]]) -> List[str]:
    if not rows:
        return []
    lefts = [name for name, _ in rows]
    maxw = max(len(x) for x in lefts)
    lines = [f"{name.ljust(maxw)}  / {usd:.2f}$" for name, usd in rows]
    return [title] + lines


async def build_portfolio_message(client: httpx.AsyncClient, key: str, secret: str, storage_dir: str) -> str:
    if not key or not secret:
        return "BINANCE_API_KEY/SECRET не заданы."

    spot = await _load_spot_balances(client, key, secret)
    earn = await _load_earn_positions(client, key, secret)
    assets = sorted(set(list(spot.keys()) + list(earn.keys())))
    prices = await _get_usd_prices(client, assets)

    # Determine CORE assets by trade_mode == LONG from /data/<PAIR>.json
    def _get_core_assets(storage_dir: str) -> set[str]:
        core = set()
        try:
            pairs = load_pairs(storage_dir)
        except Exception:
            pairs = []
        stables = ("USDC","USDT","BUSD","FDUSD")
        for pair in pairs or []:
            p = (pair or "").upper().strip()
            base = None
            for suf in stables:
                if p.endswith(suf) and len(p) > len(suf):
                    base = p[:-len(suf)]
                    break
            if not base:
                continue
            try:
                jpath = os.path.join(storage_dir, f"{p}.json")
                with open(jpath, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
                mode = (data.get("trade_mode") or "").upper()
                if mode == "LONG":
                    core.add(base)
            except Exception:
                # ignore unreadable files
                pass
        return core


    def left_label(asset: str, amt: float) -> str:
        if asset in {"USDT", "USDC"}:
            qty = f"{amt:.6f}"
        else:
            qty = f"{amt:.7f}"
        return f"{qty} {asset}"

    
    # Build Spot rows and compute percent allocation among core coins (BTC & ETH) only
    spot_items = []  # (asset, amount, usd)
    stables = {"USDT","USDC","BUSD","FDUSD"}
    spot_rows, spot_total = [], 0.0
    for a, amt in sorted(spot.items()):
        price = prices.get(a, 0.0)
        usd = amt * price if price > 0 else (amt if a in stables else 0.0)
        if usd >= 1.0:
            spot_items.append((a, amt, usd))
            spot_total += usd

    # Percent denominator = BTC+ETH only
    core = _get_core_assets(storage_dir)
    denom = sum(usd for (a, _, usd) in spot_items if a in core)

    # Append % only for BTC/ETH; BNB & USDC shown without %
    def _cat(sym: str) -> int:
        if sym in core:
            return 0
        if sym in stables:
            return 2
        return 1
    ordered = sorted(spot_items, key=lambda x: (_cat(x[0]), -x[2], x[0]))
    spot_rows = []
    for a, amt, usd in ordered:
        left = left_label(a, amt)
        if denom > 0 and a in core:
            pct = (usd / denom) * 100.0
            left = f"{left} {pct:.0f}%"
        spot_rows.append((left, usd))

# Continue with Earn computation
    earn_rows, earn_total = [], 0.0

    for a, amt in sorted(earn.items()):
        price = prices.get(a, 0.0)
        usd = amt * price if price > 0 else (amt if a in {"USDT","USDC","BUSD","FDUSD"} else 0.0)
        if usd >= 1.0:
            earn_rows.append((left_label(a, amt), usd))
            earn_total += usd

    total = spot_total + earn_total
    state = _load_state(storage_dir)
    usdc_spot = float(spot.get("USDC", 0.0))
    usdc_earn = float(earn.get("USDC", 0.0))
    state["usdc_spot"] = round(usdc_spot, 8)
    state["usdc_earn"] = round(usdc_earn, 8)
    state["usdc_total"] = round(usdc_spot + usdc_earn, 8)
    invested = float(state.get("invested_total", 0.0))
    profit = total - invested
    arrow = "⬆️" if profit > 0.01 else ("⬇️" if profit < -0.01 else "➖")
    profit_text = f"+{profit:.2f}$" if profit > 0 else f"{profit:.2f}$"

    lines: List[str] = []
    if spot_rows:
        lines += _format_block("Spot", spot_rows)
    if earn_rows:
        lines += _format_block("Earn", earn_rows)

    profit_pct = (profit / invested * 100.0) if invested > 0 else 0.0
    pct_part = f" {profit_pct:.1f}%" if invested > 0 else ""
    summary = [f"Total: {total:.2f}$", f"Invested: {invested:.2f}$", f"Profit: {profit_text}{arrow}{pct_part}"]
    _atomic_write(_storage_path(storage_dir), state)
    return "```\n" + "\n".join(lines + [""] + summary) + "\n```"
