
import time, hmac, hashlib
from typing import Dict, List
import httpx

BINANCE_API = "https://api.binance.com"

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

async def build_portfolio_message(client: httpx.AsyncClient, key: str, secret: str) -> str:
    if not key or not secret:
        return "BINANCE_API_KEY/SECRET не заданы."

    spot = await _load_spot_balances(client, key, secret)
    earn = await _load_earn_positions(client, key, secret)

    assets = sorted(set(list(spot.keys()) + list(earn.keys())))
    prices = await _get_usd_prices(client, assets)

    def fmt_line(asset: str, amt: float, usd: float) -> str:
        if asset in {"USDT","USDC"}:
            left = f"{amt:.6f} {asset:<5}"
        else:
            left = f"{amt:.7f} {asset:<5}"
        return f"{left} | {usd:7.2f} USD"

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
        lines.append("Spot — пусто (>1 USD не найдено)")

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
        lines.append("\nEarn — <1 USD или не поддерживается форматом")
    else:
        lines.append("\nEarn — нет данных (нужно разрешение Simple Earn или позиции отсутствуют)")

    total = spot_total + earn_total
    lines.append(f"\nTOTAL: {total:.2f} USD")
    return "\n".join(lines)
