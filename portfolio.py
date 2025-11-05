# portfolio.py
import time
import hmac
import hashlib
from typing import Dict, List, Tuple, Any, Optional

import httpx

STABLES = {"USDT", "USDC", "BUSD", "FDUSD", "DAI", "TUSD", "USDP"}
EARN_ALIASES = {"LDUSDC": "USDC"}  # normalize LDUSDC -> USDC

PRICES_ENDPOINT = "https://api.binance.com/api/v3/ticker/price"
ACCOUNT_ENDPOINT = "https://api.binance.com/api/v3/account"

def _sign(secret: str, qs: str) -> str:
    return hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()

async def _get_account(client: httpx.AsyncClient, key: str, secret: str) -> Dict[str, Any]:
    ts = int(time.time() * 1000)
    qs = f"timestamp={ts}&recvWindow=60000"
    sig = _sign(secret, qs)
    headers = {"X-MBX-APIKEY": key}
    url = f"{ACCOUNT_ENDPOINT}?{qs}&signature={sig}"
    r = await client.get(url, headers=headers)
    r.raise_for_status()
    return r.json()

async def _get_prices(client: httpx.AsyncClient) -> Dict[str, float]:
    r = await client.get(PRICES_ENDPOINT)
    r.raise_for_status()
    data = r.json()
    prices = {}
    for item in data:
        s = item.get("symbol")
        p = float(item.get("price"))
        prices[s] = p
    return prices

def _is_stable(asset: str) -> bool:
    a = EARN_ALIASES.get(asset, asset)
    return a in STABLES

def _spot_groups(balances: List[Dict[str, str]]) -> Tuple[List[Tuple[str, float]], List[Tuple[str, float]], List[Tuple[str, float]]]:
    """Return (bluechips, others, stables) as (asset, amount) with zeroes removed."""
    blue = []
    others = []
    stables = []
    for b in balances:
        asset = b.get("asset")
        free = float(b.get("free", "0") or 0)
        locked = float(b.get("locked", "0") or 0)
        amt = free + locked
        if amt <= 0:
            continue
        asset_norm = EARN_ALIASES.get(asset, asset)
        if asset_norm in {"BTC", "ETH"}:
            blue.append((asset_norm, amt))
        elif _is_stable(asset_norm):
            stables.append((asset_norm, amt))
        else:
            others.append((asset_norm, amt))
    # merge duplicates after aliasing
    def merge(items):
        acc = {}
        for a, v in items:
            acc[a] = acc.get(a, 0.0) + v
        # keep deterministic ordering: BTC, ETH already grouped; others by name
        return [(k, acc[k]) for k in sorted(acc.keys(), key=lambda x: (x != "BTC", x != "ETH", x))]
    return merge(blue), merge(others), merge(stables)

def _value_usd(asset: str, amt: float, prices: Dict[str, float]) -> float:
    if asset in STABLES:
        return amt  # ~1 USD
    sym = f"{asset}USDT"
    return amt * prices.get(sym, 0.0)

def _fmt_rows(rows: List[Tuple[str, float]], prices: Dict[str, float], with_pct: bool, denom: float) -> Tuple[List[str], float]:
    out = []
    total = 0.0
    for asset, amt in rows:
        usd = _value_usd(asset, amt, prices)
        total += usd
        pct = (usd / denom * 100.0) if (with_pct and denom > 0) else None
        out.append((asset, amt, usd, pct))
    if not out:
        return [], 0.0
    # width calc
    name_w = max([len(r[0]) for r in out] + [3])
    amt_w  = max([len(f"{r[1]:.8f}".rstrip('0').rstrip('.')) for r in out] + [5])
    usd_w  = max([len(f"{r[2]:,.2f}") for r in out] + [7])
    lines = []
    for asset, amt, usd, pct in out:
        amt_s = f"{amt:.8f}".rstrip('0').rstrip('.')
        usd_s = f"{usd:,.2f}"
        if pct is None:
            lines.append(f"{asset:<{name_w}}  {amt_s:>{amt_w}}  $ {usd_s:>{usd_w}}")
        else:
            pct_s = f"{pct:5.1f}%"
            lines.append(f"{asset:<{name_w}}  {amt_s:>{amt_w}}  $ {usd_s:>{usd_w}}  {pct_s:>6}")
    return lines, total

async def build_portfolio_message(client: httpx.AsyncClient, key: str, secret: str, proxy_url: Optional[str] = None) -> str:
    acct = await _get_account(client, key, secret)
    balances = acct.get("balances", [])
    prices = await _get_prices(client)

    blue, others, stables = _spot_groups(balances)

    # totals excluding stables for %
    tmp_lines, blue_total = _fmt_rows(blue, prices, with_pct=False, denom=1.0)
    tmp_lines, other_total = _fmt_rows(others, prices, with_pct=False, denom=1.0)
    non_stable_total = blue_total + other_total

    blue_lines, _ = _fmt_rows(blue, prices, with_pct=True, denom=non_stable_total)
    other_lines, _ = _fmt_rows(others, prices, with_pct=True, denom=non_stable_total)
    st_lines, st_total = _fmt_rows(stables, prices, with_pct=False, denom=1.0)

    lines = []
    if blue_lines:
        lines.append("BLUE-CHIPS")
        lines.extend(blue_lines)
        lines.append("")
    if other_lines:
        lines.append("ALTS")
        lines.extend(other_lines)
        lines.append("")
    if st_lines:
        lines.append("STABLES / EARN")
        lines.extend(st_lines)
        lines.append("")

    # Totals
    lines.append(f"Total (non-stables): $ {non_stable_total:,.2f}")
    lines.append(f"Stables total:       $ {st_total:,.2f}")
    lines.append(f"Portfolio total:     $ {non_stable_total + st_total:,.2f}")

    return "\\n".join(lines)
