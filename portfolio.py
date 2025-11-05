
import os
import time
import hmac
import hashlib
from typing import Dict, List, Tuple

import httpx

BINANCE_API = "https://api.binance.com"

STABLES = {"USDT","USDC","FDUSD","TUSD","BUSD","DAI"}
FIATS   = {"EUR","AEUR"}

# Some assets come with prefixes (e.g. LDUSDC from Earn). Normalize to spot tickers.
NORM = {
    "LDUSDC": "USDC",
    "USDSB": "USDT",  # legacy
}

def _sign(query: str, secret: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

async def _signed_get(client: httpx.AsyncClient, path: str, key: str, secret: str, params: Dict | None = None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 60_000
    q = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sig = _sign(q, secret)
    return await client.get(f"{BINANCE_API}{path}?{q}&signature={sig}", headers={"X-MBX-APIKEY": key})

async def _price_usd(client: httpx.AsyncClient, asset: str) -> float:
    # Return price in USDT (≈USD). For stables price≈1, for EUR ask EURUSDT.
    if asset in STABLES:
        return 1.0
    if asset in FIATS:
        sym = f"{asset}USDT"
    else:
        sym = f"{asset}USDT"
    r = await client.get(f"{BINANCE_API}/api/v3/ticker/price", params={"symbol": sym})
    if r.status_code != 200:
        return 0.0
    return float(r.json().get("price", 0.0))

def _trim_amount(x: float) -> str:
    # show up to 8 decimals, strip zeros
    s = f"{x:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"

def _fmt_row(qty: float, sym: str, usd: float, pct: float | None, width: int) -> str:
    qty_s = _trim_amount(qty)
    usd_s = f"{usd:,.2f}$".replace(",", " ")
    if pct is None:
        pct_s = "    "  # 4 spaces align with "41.9%"
    else:
        pct_s = f"{pct:>4.1f}%"
    return f"{qty_s:>12} {sym:<5} {pct_s}  | {usd_s:>{width}}"

async def build_portfolio_card(client: httpx.AsyncClient, key: str, secret: str) -> str:
    # 1) balances
    acc = await _signed_get(client, "/api/v3/account", key, secret)
    acc.raise_for_status()
    balances = acc.json().get("balances", [])

    # 2) aggregate and normalize
    amounts: Dict[str, float] = {}
    for b in balances:
        free = float(b.get("free", "0"))
        locked = float(b.get("locked", "0"))
        total = free + locked
        if total <= 0:
            continue
        a = b["asset"]
        a = NORM.get(a, a)
        amounts[a] = amounts.get(a, 0.0) + total

    # 3) compute USD values, drop dust (<$1)
    usd_by_asset: Dict[str, float] = {}
    for a, q in list(amounts.items()):
        p = await _price_usd(client, a)
        usd = q * p if a not in STABLES else q  # for stables p≈1
        if usd < 1.0:
            continue
        usd_by_asset[a] = usd
        amounts[a] = q

    # split categories
    non_stables = [(a, amounts[a], usd_by_asset[a]) for a in usd_by_asset if a not in STABLES | FIATS]
    stables     = [(a, amounts[a], usd_by_asset[a]) for a in usd_by_asset if a in STABLES]
    fiats       = [(a, amounts[a], usd_by_asset[a]) for a in usd_by_asset if a in FIATS]

    non_stables.sort(key=lambda x: (x[0] not in {"BTC","ETH"}, -x[2]))  # BTC/ETH first, then by usd desc
    stables.sort(key=lambda x: -x[2])
    fiats.sort(key=lambda x: -x[2])

    total_non_stables = sum(u for _,_,u in non_stables)
    total_stables = sum(u for _,_,u in stables)
    total_fiats = sum(u for _,_,u in fiats)
    portfolio_total = total_non_stables + total_stables + total_fiats

    # width for right column
    right_width = max(8, len(f"{portfolio_total:,.2f}$".replace(",", " ")))

    lines: List[str] = []
    lines.append("Spot")

    # non-stables with percentages
    if non_stables:
        for a, qty, usd in non_stables:
            pct = (usd / total_non_stables * 100.0) if total_non_stables > 0 else None
            lines.append(_fmt_row(qty, a, usd, pct, right_width))

    # fiats (EUR etc.) as separate group if present
    if fiats:
        lines.append("\nFIAT")
        for a, qty, usd in fiats:
            lines.append(_fmt_row(qty, a, usd, None, right_width))

    # stables in Spot
    if stables:
        lines.append("\nSTABLES / EARN")
        for a, qty, usd in stables:
            lines.append(_fmt_row(qty, a, usd, None, right_width))

    lines.append("")
    lines.append(f"Total (non-stables):   {total_non_stables:>{right_width}.2f}$")
    lines.append(f"Stables total:         {total_stables:>{right_width}.2f}$")
    if total_fiats:
        lines.append(f"Fiats total:           {total_fiats:>{right_width}.2f}$")
    lines.append(f"Portfolio total:       {portfolio_total:>{right_width}.2f}$")

    return "\n".join(lines)
