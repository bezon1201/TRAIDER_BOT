# portfolio.py
import os, time, hmac, hashlib, json
from typing import Dict, List, Tuple
import requests

BINANCE_API = "https://api.binance.com"
STABLES = {"USDT","USDC","BUSD","FDUSD","TUSD","USDP","DAI"}

def _sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

def _signed_get(path: str, key: str, secret: str, params: Dict[str, str] | None = None) -> Dict:
    if params is None:
        params = {}
    params["timestamp"] = str(int(time.time() * 1000))
    query = "&".join(f"{k}={v}" for k, v in params.items())
    sig = _sign(secret, query)
    headers = {"X-MBX-APIKEY": key}
    r = requests.get(f"{BINANCE_API}{path}", params={**params, "signature": sig}, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

def _get_spot_balances(key: str, secret: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    data = _signed_get("/api/v3/account", key, secret, params={"recvWindow":"60000"})
    for b in data.get("balances", []):
        asset = b.get("asset")
        if not asset: 
            continue
        free = float(b.get("free", 0))
        locked = float(b.get("locked", 0))
        total = free + locked
        if total > 0:
            out[asset] = out.get(asset, 0.0) + total
    return out

def _get_earn_balances(key: str, secret: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    # Flexible
    try:
        flex = _signed_get("/sapi/v1/simple-earn/flexible/position", key, secret, params={"size":"100"})
        rows = flex.get("rows", flex if isinstance(flex, list) else [])
        for p in rows:
            asset = p.get("asset") or p.get("assetSymbol") or p.get("assetName")
            total = float(p.get("total") or p.get("totalAmount") or p.get("freeAmount") or 0)
            if asset and total > 0:
                out[asset] = out.get(asset, 0.0) + total
    except Exception:
        pass
    # Locked
    try:
        locked = _signed_get("/sapi/v1/simple-earn/locked/position", key, secret, params={"size":"100"})
        rows = locked.get("rows", locked if isinstance(locked, list) else [])
        for p in rows:
            asset = p.get("asset") or p.get("assetSymbol") or p.get("assetName")
            total = float(p.get("totalAmount") or p.get("purchasedAmount") or 0)
            if asset and total > 0:
                out[asset] = out.get(asset, 0.0) + total
    except Exception:
        pass
    return out

def _get_price_usd(symbol: str) -> float:
    if symbol in STABLES:
        return 1.0
    for quote in ("USDT","USDC","FDUSD","BUSD"):
        pair = f"{symbol}{quote}"
        try:
            r = requests.get(f"{BINANCE_API}/api/v3/ticker/price", params={"symbol": pair}, timeout=10)
            if r.status_code == 200:
                j = r.json()
                if "price" in j:
                    return float(j["price"])
        except requests.RequestException:
            continue
    return 0.0

def _format_rows(block_title: str, rows: List[Tuple[str, float, float, int]]) -> List[str]:
    # rows: (asset, amount, usd_value, pct_for_non_stable_int_or_-1_for_stable)
    lefts = []
    for asset, amount, _, pct in rows:
        if asset in STABLES:
            qty = f"{amount:.6f}"
            left = f"{qty} {asset}"
        else:
            qty = f"{amount:.7f}"
            if pct >= 0:
                left = f"{qty} {asset} {pct}%"
            else:
                left = f"{qty} {asset}"
        lefts.append(left)
    width = max(len(s) for s in lefts) if lefts else 0
    lines = [block_title]
    for (asset, amount, usd, pct), left in zip(rows, lefts):
        right = f"{usd:.2f}$"
        lines.append(left.ljust(width) + "  | " + right)
    return lines

def _load_state(storage_dir: str) -> Dict:
    os.makedirs(storage_dir, exist_ok=True)
    path = os.path.join(storage_dir, "portfolio.json")
    if not os.path.exists(path):
        return {"invested_total": 0.0, "history": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"invested_total": 0.0, "history": []}

def _save_state(storage_dir: str, state: Dict) -> None:
    os.makedirs(storage_dir, exist_ok=True)
    path = os.path.join(storage_dir, "portfolio.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)

def generate_portfolio_text(api_key: str, api_secret: str, storage_dir: str = "/tmp") -> str:
    spot = _get_spot_balances(api_key, api_secret)
    earn = _get_earn_balances(api_key, api_secret)

    # Build price map only for assets we have
    assets = set(spot.keys()) | set(earn.keys())
    prices: Dict[str, float] = {}
    for a in assets:
        prices[a] = _get_price_usd(a)

    # Build Spot rows with filtering < $1.00
    spot_rows_raw: List[Tuple[str, float, float]] = []
    for a, amount in spot.items():
        usd = amount * (prices.get(a, 0.0) or 0.0)
        if usd >= 1.0:
            spot_rows_raw.append((a, amount, usd))

    # Sort: BTC, ETH first (preserve order), then other non-stables by USD desc, then stables by USD desc
    def is_stable(a: str) -> bool: return a in STABLES
    top = [r for r in spot_rows_raw if r[0] in ("BTC","ETH")]
    others_nonstable = [r for r in spot_rows_raw if (r[0] not in ("BTC","ETH") and not is_stable(r[0]))]
    stables = [r for r in spot_rows_raw if is_stable(r[0])]
    others_nonstable.sort(key=lambda x: x[2], reverse=True)
    stables.sort(key=lambda x: x[2], reverse=True)
    ordered_spot = top + others_nonstable + stables

    # Percentages: only for non-stables, relative to sum of non-stables
    nonstable_sum_usd = sum(usd for (a, _, usd) in ordered_spot if not is_stable(a))
    spot_rows_fmt: List[Tuple[str, float, float, int]] = []
    for a, amount, usd in ordered_spot:
        if not is_stable(a) and nonstable_sum_usd > 0:
            pct = int(round(usd / nonstable_sum_usd * 100))
        else:
            pct = -1
        spot_rows_fmt.append((a, amount, usd, pct))

    # Earn rows
    earn_rows_raw: List[Tuple[str, float, float]] = []
    for a, amount in earn.items():
        usd = amount * (prices.get(a, 0.0) or 0.0)
        if usd >= 1.0:
            earn_rows_raw.append((a, amount, usd))
    earn_rows_raw.sort(key=lambda x: x[2], reverse=True)
    earn_rows_fmt: List[Tuple[str, float, float, int]] = []
    for a, amount, usd in earn_rows_raw:
        earn_rows_fmt.append((a, amount, usd, -1))

    # Totals
    total = sum(usd for _,_,usd in spot_rows_fmt) + sum(usd for _,_,usd in earn_rows_fmt)
    state = _load_state(storage_dir)
    invested = float(state.get("invested_total") or 0.0)
    profit = total - invested
    arrow = "⬆️" if profit > 0.01 else ("⬇️" if profit < -0.01 else "➖")
    profit_text = f"+{profit:.2f}$" if profit > 0 else f"{profit:.2f}$"
    profit_pct = (profit / invested * 100.0) if invested > 0 else 0.0
    pct_part = f"{profit_pct:.1f}%" if invested > 0 else ""

    lines: List[str] = []
    if spot_rows_fmt:
        lines += _format_rows("Spot", spot_rows_fmt)
    if earn_rows_fmt:
        lines += _format_rows("Earn", earn_rows_fmt)

    summary = [
        f"",
        f"Total:    {total:.2f}$",
        f"Invested: {invested:.2f}$",
        f"Profit:  {profit_text}{arrow}{pct_part}",
    ]
    return "```\n" + "\n".join(lines + summary) + "\n```"
