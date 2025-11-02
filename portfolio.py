import os, json, time, hmac, hashlib, tempfile
from typing import Dict, List
import httpx

BINANCE_API = "https://api.binance.com"

# ---------- Storage helpers ----------
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

# ---------- Binance helpers ----------
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
    # Flexible
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
    # Locked
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

# ---------- Public API ----------
async def build_portfolio_message(client: httpx.AsyncClient, key: str, secret: str, storage_dir: str) -> str:
    if not key or not secret:
        return "BINANCE_API_KEY/SECRET не заданы."

    spot = await _load_spot_balances(client, key, secret)
    earn = await _load_earn_positions(client, key, secret)

    assets = sorted(set(list(spot.keys()) + list(earn.keys())))
    prices = await _get_usd_prices(client, assets)

    def fmt_line(asset: str, amt: float, usd: float) -> str:
        if asset in {"USDT","USDC"}:
            left = f"{amt:.6f} {asset}"
        else:
            left = f"{amt:.7f} {asset}"
        return f"{left} - {usd:.2f}$"

    lines = ["HOLDINGS:"]

    spot_lines, spot_total = [], 0.0
    for a, amt in sorted(spot.items()):
        price = prices.get(a, 0.0)
        usd = amt * price if price > 0 else (amt if a in {"USDT","USDC","BUSD","FDUSD"} else 0.0)
        if usd >= 1.0:
            spot_lines.append(fmt_line(a, amt, usd))
            spot_total += usd

    if spot_lines:
        lines.append("Spot")
        lines.extend(spot_lines)
    else:
        lines.append("Spot — пусто (>1$ не найдено)")

    earn_lines, earn_total = [], 0.0
    if earn:
        for a, amt in sorted(earn.items()):
            price = prices.get(a, 0.0)
            usd = amt * price if price > 0 else (amt if a in {"USDT","USDC","BUSD","FDUSD"} else 0.0)
            if usd >= 1.0:
                earn_lines.append(fmt_line(a, amt, usd))
                earn_total += usd

    if earn_lines:
        lines.append("\nEarn")
        lines.extend(earn_lines)
    elif earn:
        lines.append("\nEarn — <1$ или не поддерживается форматом")
    else:
        lines.append("\nEarn — нет данных (нужно разрешение Simple Earn или позиции отсутствуют)")

    total = spot_total + earn_total

    # invested from storage
    state = _load_state(storage_dir)
    invested = float(state.get("invested_total", 0.0))
    profit = total - invested

    lines.append(f"\nTotal: {total:.2f}$")
    lines.append(f"Invested: {invested:.2f}$")
    lines.append(f"Profit: {profit:.2f}$")

    return "\n".join(lines)
