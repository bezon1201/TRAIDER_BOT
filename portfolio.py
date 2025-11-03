
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

    def left_label(asset: str, amt: float) -> str:
        if asset in {"USDT", "USDC"}:
            qty = f"{amt:.6f}"
        else:
            qty = f"{amt:.7f}"
        return f"{qty} {asset}"

    
    # Build Spot rows and compute percent allocation among coins from pairs.json (exclude stables like USDC)
        spot_items = []  # (asset, amount, usd)
    stables = {"USDT","USDC","BUSD","FDUSD"}
    spot_rows, spot_total = [], 0.0
    for a, amt in sorted(spot.items()):
        price = prices.get(a, 0.0)
        usd = amt * price if price > 0 else (amt if a in stables else 0.0)
        if usd >= 1.0:
            spot_items.append((a, amt, usd))
            spot_total += usd

    # Calculate denominator from ALL spot assets > $1 excluding only USDC
    denom = sum(usd for (a, _, usd) in spot_items if a != "USDC")

    # Rebuild spot_rows with percentage for non-USDC assets
    spot_rows = []
    for a, amt, usd in spot_items:
        left = left_label(a, amt)
        if denom > 0 and a != "USDC":
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
    pct_part = f" - {profit_pct:.1f}%" if invested > 0 else ""
    summary = [f"Total: {total:.2f}$", f"Invested: {invested:.2f}$", f"Profit: {profit_text}{arrow}{pct_part}"]
    return "```\n" + "\n".join(lines + [""] + summary) + "\n```"
