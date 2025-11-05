# portfolio.py
import os
import time
import hmac
import hashlib
from typing import Dict, Optional, List, Tuple
from decimal import Decimal, ROUND_DOWN

import httpx

STABLES = {"USDC", "USDT", "FDUSD", "TUSD", "DAI", "BUSD"}
BINANCE_API_BASE = "https://api.binance.com"
SAPI_BASE = "https://api.binance.com"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _sign(query: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


async def _signed_get(client: httpx.AsyncClient, path: str, api_key: str, api_secret: str, params: Optional[Dict[str, str]] = None):
    if params is None:
        params = {}
    params["timestamp"] = str(_now_ms())
    params.setdefault("recvWindow", "5000")
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
    sig = _sign(qs, api_secret)
    url = f"{SAPI_BASE}{path}?{qs}&signature={sig}"
    headers = {"X-MBX-APIKEY": api_key}
    try:
        return await client.get(url, headers=headers)
    except Exception:
        return None


async def fetch_spot_balances(client: httpx.AsyncClient, api_key: str, api_secret: str) -> Dict[str, Decimal]:
    try:
        ts = _now_ms()
        query = f"timestamp={ts}&recvWindow=5000"
        sig = _sign(query, api_secret)
        url = f"{BINANCE_API_BASE}/api/v3/account?{query}&signature={sig}"
        headers = {"X-MBX-APIKEY": api_key}
        r = await client.get(url, headers=headers)
        if r.status_code != 200:
            return {}
        data = r.json()
    except Exception:
        return {}
    out: Dict[str, Decimal] = {}
    for b in data.get("balances", []):
        asset = b.get("asset")
        try:
            amt = Decimal(b.get("free", "0")) + Decimal(b.get("locked", "0"))
        except Exception:
            continue
        if amt > Decimal("0"):
            out[asset] = out.get(asset, Decimal("0")) + amt
    return out


async def fetch_earn_balances(client: httpx.AsyncClient, api_key: str, api_secret: str) -> Dict[str, Decimal]:
    out: Dict[str, Decimal] = {}
    # Simple Earn Flexible
    r = await _signed_get(client, "/sapi/v1/simple-earn/flexible/positions", api_key, api_secret, params={"size": "100"})
    if r and r.status_code == 200:
        try:
            j = r.json()
            rows = j.get("rows", j if isinstance(j, list) else [])
            for it in rows:
                asset = it.get("asset")
                raw = it.get("totalAmount") or it.get("amount") or "0"
                amt = Decimal(str(raw))
                if amt > 0:
                    out[asset] = out.get(asset, Decimal("0")) + amt
        except Exception:
            pass
    # Simple Earn Locked
    r = await _signed_get(client, "/sapi/v1/simple-earn/locked/positions", api_key, api_secret, params={"size": "100"})
    if r and r.status_code == 200:
        try:
            j = r.json()
            rows = j.get("rows", j if isinstance(j, list) else [])
            for it in rows:
                asset = it.get("asset")
                raw = it.get("totalAmount") or it.get("amount") or "0"
                amt = Decimal(str(raw))
                if amt > 0:
                    out[asset] = out.get(asset, Decimal("0")) + amt
        except Exception:
            pass
    # Legacy Savings fallback
    if not out:
        r = await _signed_get(client, "/sapi/v1/lending/daily/token/position", api_key, api_secret)
        if r and r.status_code == 200:
            try:
                for it in r.json():
                    asset = it.get("asset")
                    raw = it.get("totalAmount") or it.get("freeAmount") or it.get("amount") or "0"
                    amt = Decimal(str(raw))
                    if amt > 0:
                        out[asset] = out.get(asset, Decimal("0")) + amt
            except Exception:
                pass
    return out


class PriceBook:
    def __init__(self) -> None:
        self.cache: Dict[str, Decimal] = {}

    async def get(self, client: httpx.AsyncClient, symbol: str):
        if symbol in self.cache:
            return self.cache[symbol]
        url = f"{BINANCE_API_BASE}/api/v3/ticker/price?symbol={symbol}"
        try:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            px = Decimal(r.json()["price"])
            self.cache[symbol] = px
            return px
        except Exception:
            return None


async def price_in_usdc(client: httpx.AsyncClient, book: PriceBook, asset: str):
    if asset == "USDC":
        return Decimal("1")
    p = await book.get(client, f"{asset}USDC")
    if p:
        return p
    p1 = await book.get(client, f"{asset}USDT")
    if p1:
        p2 = await book.get(client, "USDTUSDC")
        if p2:
            return p1 * p2
        p3 = await book.get(client, "USDCUSDT")
        if p3 and p3 > 0:
            return p1 / p3
    p_inv = await book.get(client, f"USDC{asset}")
    if p_inv and p_inv > 0:
        return Decimal("1") / p_inv
    if asset in STABLES:
        return Decimal("1")
    return None


def _fmt_amount(asset: str, amt: Decimal) -> str:
    if amt == 0:
        return "0"
    places = 8 if amt < Decimal("1") else (6 if amt < Decimal("100") else 2)
    quant = Decimal(f"1e-{places}")
    return (amt.quantize(quant, rounding=ROUND_DOWN).normalize()).to_eng_string()


def _fmt_money(n: Decimal) -> str:
    return f"{n.quantize(Decimal('0.01'), rounding=ROUND_DOWN):f}$"


def _is_stable(asset: str) -> bool:
    return asset in STABLES


async def build_portfolio_message() -> str:
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    storage_dir = os.environ.get("STORAGE_DIR", "/data")

    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=True) as client:
        spot = await fetch_spot_balances(client, api_key, api_secret) if api_key and api_secret else {}
        earn = await fetch_earn_balances(client, api_key, api_secret) if api_key and api_secret else {}

        book = PriceBook()

        # Build Spot entries
        spot_items: List[Tuple[str, Decimal, Decimal, bool]] = []
        spot_nonstable_sum = Decimal("0")
        for asset, amt in spot.items():
            pr = await price_in_usdc(client, book, asset)
            if pr is None:
                continue
            val = amt * pr
            if val < Decimal("1"):
                continue
            is_stable = _is_stable(asset)
            if not is_stable:
                spot_nonstable_sum += val
            spot_items.append((asset, amt, val, is_stable))

        # Order Spot
        btc = [x for x in spot_items if x[0] == "BTC"]
        eth = [x for x in spot_items if x[0] == "ETH"]
        rest_non = [x for x in spot_items if (not x[3] and x[0] not in {"BTC","ETH"})]
        stables = [x for x in spot_items if x[3]]
        rest_non.sort(key=lambda t: t[2], reverse=True)
        stables.sort(key=lambda t: t[2], reverse=True)
        ordered_spot = btc + eth + rest_non + stables

        # Earn
        earn_items: List[Tuple[str, Decimal, Decimal]] = []
        for asset, amt in earn.items():
            pr = await price_in_usdc(client, book, asset)
            if pr is None:
                continue
            val = amt * pr
            if val < Decimal("1"):
                continue
            earn_items.append((asset, amt, val))
        earn_items.sort(key=lambda t: t[2], reverse=True)

        total_spot = sum(v for (_a,_b,v,_s) in ordered_spot)
        total_earn = sum(v for (_a,_b,v) in earn_items)
        total = total_spot + total_earn

        # Read invested
        invested = None
        try:
            os.makedirs(storage_dir, exist_ok=True)
            p = os.path.join(storage_dir, "portfolio.json")
            if os.path.exists(p):
                import json
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "invested_usdc" in data:
                    invested = Decimal(str(data["invested_usdc"]))
        except Exception:
            invested = None

        # Format
        lines: List[str] = []
        amt_w, sym_w, pct_w = 14, 6, 4

        lines.append("Spot")
        for asset, amt, val, is_stable in ordered_spot:
            amt_s = _fmt_amount(asset, amt).rjust(amt_w)
            sym_s = asset.ljust(sym_w)
            if not is_stable and spot_nonstable_sum > 0:
                pct = (val / spot_nonstable_sum * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_DOWN)
                pct_s = f"{pct}%".rjust(pct_w)
            else:
                pct_s = " " * pct_w
            val_s = _fmt_money(val)
            lines.append(f"{amt_s} {sym_s} {pct_s} | {val_s}")

        if earn_items:
            lines.append("Earn")
            for asset, amt, val in earn_items:
                amt_s = _fmt_amount(asset, amt).rjust(amt_w)
                sym_s = asset.ljust(sym_w)
                val_s = _fmt_money(val)
                lines.append(f"{amt_s} {sym_s}     | {val_s}")

        lines.append("")
        lines.append(f"Total:    {_fmt_money(total)}")
        if invested is not None:
            profit = total - invested
            arrow = "⬆️" if profit >= 0 else "⬇️"
            pct = (abs(profit) / invested * Decimal(100)).quantize(Decimal('0.1'), rounding=ROUND_DOWN) if invested > 0 else Decimal("0.0")
            sign = "" if profit >= 0 else "-"
            lines.append(f"Invested: {_fmt_money(invested)}")
            lines.append(f"Profit:   {sign}{_fmt_money(abs(profit))}{arrow}{sign}{pct}%")
        else:
            lines.append("Invested: n/a")
            lines.append("Profit:   n/a")

        return "\n".join(lines)
