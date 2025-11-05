import time, hmac, hashlib, urllib.parse as ul, json
from typing import Dict, List, Tuple
import httpx

# ---------- Normalization ----------
STABLES = {"USDT","USDC","BUSD","FDUSD","USDP","TUSD","DAI","AEUR","USDE"}

# map wrappers -> base
MAPPING = {
    "LDUSDT":"USDT", "FDUSDT":"USDT",
    "LDUSDC":"USDC", "FDUSDC":"USDC",
    "WBETH":"ETH", "BETH":"ETH",
    "WBTC":"BTC", "BTCB":"BTC",
}

def normalize_asset(a: str) -> str:
    a = (a or "").upper()
    if a in MAPPING: return MAPPING[a]
    # General LD/FD prefix (e.g., LDUSDC) -> drop prefix
    if a.startswith("LD") or a.startswith("FD"):
        base = a[2:]
        if base: return MAPPING.get(a, base)
    return a

# ---------- Binance helpers ----------
def _sign(q: str, secret: str) -> str:
    import hmac, hashlib
    return hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()

async def _binance_get(client: httpx.AsyncClient, path: str, params: dict, key: str, secret: str) -> dict:
    ms = int(time.time()*1000)
    params = dict(params or {})
    params.update({"timestamp": ms, "recvWindow": 60000})
    q = ul.urlencode(params)
    sig = _sign(q, secret)
    headers = {"X-MBX-APIKEY": key}
    url = "https://api.binance.com" + path
    r = await client.get(url, params={**params, "signature": sig}, headers=headers, timeout=20.0)
    r.raise_for_status()
    return r.json()

async def _load_spot_balances(client: httpx.AsyncClient, key: str, secret: str) -> Dict[str, float]:
    data = await _binance_get(client, "/api/v3/account", {}, key, secret)
    balances = {}
    for b in data.get("balances", []):
        total = float(b["free"]) + float(b["locked"])
        if total > 0:
            a = normalize_asset(b["asset"])
            balances[a] = balances.get(a, 0.0) + total
    return balances

async def _load_earn_positions(client: httpx.AsyncClient, key: str, secret: str) -> Dict[str, float]:
    out: Dict[str,float] = {}
    # try flexible
    try:
        data = await _binance_get(client, "/sapi/v1/simple-earn/flexible/position", {"current":1,"size":100}, key, secret)
        for p in data.get("rows", []):
            a = normalize_asset(p["asset"])
            amt = float(p.get("totalAmount", p.get("amount", 0)) or 0)
            if amt>0: out[a] = out.get(a, 0.0) + amt
    except Exception:
        pass
    # try locked
    try:
        data = await _binance_get(client, "/sapi/v1/simple-earn/locked/position", {"current":1,"size":100}, key, secret)
        for p in data.get("rows", []):
            a = normalize_asset(p["asset"])
            amt = float(p.get("amount", 0) or 0)
            if amt>0: out[a] = out.get(a, 0.0) + amt
    except Exception:
        pass
    return out

async def _get_usd_prices(client: httpx.AsyncClient, assets: List[str]) -> Dict[str, float]:
    prices: Dict[str,float] = {}
    for a in assets:
        base = normalize_asset(a)
        if base in STABLES:
            prices[base] = 1.0
            continue
        sym = f"{base}USDT"
        try:
            r = await client.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": sym}, timeout=10.0)
            if r.status_code == 200:
                prices[base] = float(r.json()["price"])
                continue
        except Exception:
            pass
        # fallback: try USDTBASE (rarely useful)
        try:
            r = await client.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": f"USDT{base}"}, timeout=10.0)
            if r.status_code == 200:
                p = float(r.json()["price"])
                prices[base] = 1.0/p if p>0 else 0.0
        except Exception:
            prices[base] = 0.0
    return prices

def _fmt_amounts(rows: List[Tuple[str,float,float]], stables_bottom=True) -> str:
    # rows: (asset, amount, usd)
    def is_stable(a): return a in STABLES
    # widths
    amt_s = [s for _,s,_ in rows]
    sym_s = [a for a,_,_ in rows]
    usd_s = [u for *_,u in rows]
    # dynamic amount text with up to 8 decimals, trimmed
    def fmt_amt(x):
        s = f"{x:.8f}".rstrip("0").rstrip(".")
        # keep at least one decimal for non-integers already handled
        return s
    a_strs = [fmt_amt(v) for _,v,_ in rows]
    usd_strs = [f"{v:.2f}$" for *_,v in rows]
    w_amt = max(len(s) for s in a_strs) if a_strs else 1
    w_sym = max(len(s) for s in sym_s) if sym_s else 3
    w_usd = max(len(s) for s in usd_strs) if usd_strs else 1
    # build text lines
    out = []
    for (asset, amount, usd), a_s, usd_s in zip(rows, a_strs, usd_strs):
        out.append(f"{a_s:>{w_amt}} {asset:<{w_sym}} {{PCT}}  | {usd_s:>{w_usd}}")
    return "\n".join(out), w_amt, w_sym, w_usd

def _split_rows(rows: List[Tuple[str,float,float]]):
    nonstable = [r for r in rows if r[0] not in STABLES]
    stables = [r for r in rows if r[0] in STABLES]
    return nonstable, stables

async def build_portfolio_message(client: httpx.AsyncClient, key: str, secret: str, storage_dir: str) -> str:
    spot = await _load_spot_balances(client, key, secret)
    earn = await _load_earn_positions(client, key, secret)

    assets = sorted(set(spot) | set(earn))
    prices = await _get_usd_prices(client, list(assets))

    rows_spot: List[Tuple[str,float,float]] = []
    for a, qty in spot.items():
        base = normalize_asset(a)
        usd = qty * float(prices.get(base, 0.0))
        rows_spot.append((base, qty, usd))

    rows_earn: List[Tuple[str,float,float]] = []
    for a, qty in earn.items():
        base = normalize_asset(a)
        usd = qty * float(prices.get(base, 0.0))
        rows_earn.append((base, qty, usd))

    # sort: BTC, ETH first; then by usd desc; then stables last
    nonstable, stables = _split_rows(rows_spot)
    # BTC/ETH pin
    pin = {"BTC": -2, "ETH": -1}
    nonstable.sort(key=lambda r: (pin.get(r[0], 0), -r[2], r[0]))
    stables.sort(key=lambda r: (r[0] != "USDT", r[0]))
    rows_spot_sorted = nonstable + stables

    # compute pct only for non-stables
    sum_nonstable = sum(usd for a,_,usd in nonstable) or 0.0
    lines, w_amt, w_sym, w_usd = _fmt_amounts(rows_spot_sorted)
    if sum_nonstable > 0:
        pct_map = {a: int(round((usd/sum_nonstable)*100)) for a,_,usd in nonstable}
    else:
        pct_map = {}

    # inject pct strings
    out_lines = []
    for L in lines.splitlines():
        # L like: "<amt> <SYM> {PCT}  | <usd>$"
        asset = L.split()[1] if len(L.split())>=2 else ""
        pct_str = (f"{pct_map.get(asset,''):>2}%" if asset in pct_map else "   ")
        out_lines.append(L.replace("{PCT}", pct_str))

    # totals
    total_spot = sum(usd for *_,usd in rows_spot_sorted)
    total_earn = sum(usd for *_,usd in rows_earn)
    total = total_spot + total_earn

    # invested (optional state file)
    state_path = os.path.join(storage_dir or "/data", "state.json")
    invested = 0.0
    try:
        if os.path.exists(state_path):
            invested = float((json.loads(open(state_path,"r",encoding="utf-8").read()).get("invested_total") or 0.0))
    except Exception:
        invested = 0.0

    profit = total - invested
    arrow = "⬆️" if profit >= 0 else "⬇️"
    pct_part = f"{(profit/invested*100):.1f}%" if invested > 0 else ""

    # format earn lines
    lines_earn, _, _, _ = _fmt_amounts(rows_earn)
    body = "Spot\n" + "\n".join(out_lines)
    if rows_earn:
        body += "\nEarn\n" + "\n".join([l.replace("{PCT}","   ") for l in lines_earn.splitlines()])
    body += f"\n\nTotal:    {total:.2f}$\nInvested: {invested:.2f}$\nProfit:   {profit:.2f}${arrow}{pct_part}"
    # HTML <pre> ensures monospace alignment in Telegram
    return f"<pre>{body}</pre>"
