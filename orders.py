from __future__ import annotations
from datetime import datetime
from typing import Tuple, Dict, Any
import os, json, time, hmac, hashlib

# ---- Levels constant (used across orders module) ----
LEVEL_KEYS = ['L0','L1','L2','L3']


import httpx
from confyg import load_confyg
from portfolio import refresh_usdc_trade_free, get_usdc_spot_earn_total

from budget import get_pair_budget, get_pair_levels, save_pair_levels, recompute_pair_aggregates, set_pair_week
from auto_flags import compute_all_flags
from symbol_info import build_symbol_message
import math

# Недельные доли по режиму рынка
WEEKLY_PERCENT = {
    "UP":   {"OCO": 10, "L0": 10, "L1": 5,  "L2": 0,  "L3": 0},
    "RANGE":{"OCO": 5,  "L0": 5,  "L1": 10, "L2": 5,  "L3": 0},
    "DOWN": {"OCO": 5,  "L0": 0,  "L1": 5, "L2": 10, "L3": 5},
}

BINANCE_API = "https://api.binance.com"


def _sign_binance(query: str, secret: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

BINANCE_API = "https://api.binance.com"

def _binance_signed_get(path: str, key: str, secret: str, params: dict) -> dict:
    ts = int(time.time() * 1000)
    q = dict(params or {})
    q["timestamp"] = ts
    q.setdefault("recvWindow", 5000)
    items = "&".join(f"{k}={q[k]}" for k in sorted(q))
    sig = _sign_binance(items, secret)
    url = f"{BINANCE_API}{path}?{items}&signature={sig}"
    headers = {"X-MBX-APIKEY": key}
    r = httpx.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}

def _binance_signed_post(path: str, key: str, secret: str, params: dict) -> dict:
    ts = int(time.time() * 1000)
    q = dict(params or {})
    q["timestamp"] = ts
    q.setdefault("recvWindow", 5000)
    items = "&".join(f"{k}={q[k]}" for k in sorted(q))
    sig = _sign_binance(items, secret)
    url = f"{BINANCE_API}{path}?{items}&signature={sig}"
    headers = {"X-MBX-APIKEY": key, "Content-Type": "application/x-www-form-urlencoded"}
    r = httpx.post(url, headers=headers, timeout=15)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


def binance_redeem_flexible(asset: str, amount: float):
    """
    Redeem from Simple Earn Flexible for given asset.
    Returns (ok, data/requestId_or_error).
    """
    key = os.getenv("BINANCE_API_KEY", "").strip()
    secret = os.getenv("BINANCE_API_SECRET", "").strip()
    if not key or not secret:
        return False, {"error": "no_api_keys"}

    try:
        pos = _binance_signed_get("/sapi/v1/simple-earn/flexible/position", key, secret, params={"size": 100})
        rows = pos.get("rows") if isinstance(pos, dict) else pos
        if not rows:
            return False, {"error": "no_positions", "response": pos}

        product_id = None
        for p in rows:
            a = p.get("asset") or p.get("assetSymbol") or p.get("assetName")
            if a == asset:
                product_id = p.get("productId") or p.get("projectId") or p.get("subscriptionId") or p.get("positionId")
                break
        if not product_id:
            return False, {"error": "no_product_id_for_asset", "asset": asset, "response": rows}

        data = _binance_signed_post(
            "/sapi/v1/simple-earn/flexible/redeem",
            key, secret,
            params={"productId": product_id, "amount": str(amount), "redeemAll": "false"}
        )
        if isinstance(data, dict) and ("requestId" in data or data.get("success") is True):
            return True, data
        return False, data
    except Exception as e:
        return False, {"error": "exception", "detail": str(e)}

def _storage_dir() -> str:
    return os.getenv("STORAGE_DIR", "/data")


def _live_state_path() -> str:
    return os.path.join(_storage_dir(), "live_orders_state.json")


def _live_log_csv_path() -> str:
    return os.path.join(_storage_dir(), "live_orders_log.csv")


def _live_log_jsonl_path() -> str:
    return os.path.join(_storage_dir(), "live_orders_log.jsonl")


def _atomic_write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _load_live_state() -> Dict[str, Any]:
    try:
        with open(_live_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_live_state(state: Dict[str, Any]) -> None:
    _atomic_write_json(_live_state_path(), state)


def _append_live_logs(record: Dict[str, Any]) -> None:
    # CSV
    csv_path = _live_log_csv_path()
    header = "ts,symbol,side,level,amount_planned,price,qty,notional,orderId,clientOrderId,status,orderType\n"
    line = (
        f"{record.get('ts')},"
        f"{record.get('symbol')},"
        f"{record.get('side')},"
        f"{record.get('level')},"
        f"{record.get('amount_planned')},"
        f"{record.get('price')},"
        f"{record.get('qty')},"
        f"{record.get('notional')},"
        f"{record.get('orderId')},"
        f"{record.get('clientOrderId')},"
        f"{record.get('status')}," + f"{record.get('orderType')}\n"
    )
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    need_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    with open(csv_path, "a", encoding="utf-8") as f:
        if need_header:
            f.write(header)
        f.write(line)

    # JSONL
    jsonl_path = _live_log_jsonl_path()
    os.makedirs(os.path.dirname(jsonl_path) or ".", exist_ok=True)
    try:
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        # best effort: fallback to str()
        payload = str(record)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(payload + "\n")


def _is_live_pair(symbol: str) -> bool:
    """
    Check if live-mode is enabled and the given symbol is in the live pairs list.
    """
    symbol = (symbol or "").upper().strip()
    try:
        cfg = load_confyg(_storage_dir())
    except Exception:
        return False
    if not isinstance(cfg, dict):
        return False
    if not cfg.get("live"):
        return False
    try:
        pairs = [ (p or "").upper().strip() for p in (cfg.get("pairs") or []) ]
    except Exception:
        pairs = []
    return symbol in pairs


def _binance_limit_buy(symbol: str, price: float, qty: float, key: str, secret: str, client_order_id: str | None = None) -> dict:
    """
    Place a synchronous SPOT LIMIT BUY order on Binance.
    """
    params = {
        "symbol": symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "GTC",
        "quantity": f"{qty:.8f}".rstrip("0").rstrip("."),
        "price": f"{price:.8f}".rstrip("0").rstrip("."),
        "recvWindow": 10_000,
        "timestamp": int(time.time() * 1000),
    }
    if client_order_id:
        params["newClientOrderId"] = client_order_id
    # signature over sorted query string
    q = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sig = _sign_binance(q, secret)
    url = f"{BINANCE_API}/api/v3/order?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": key}
    with httpx.Client(timeout=10.0) as client:
        r = client.post(url, headers=headers)
        if r.status_code != 200:
            try:
                body = r.json()
                msg = body.get("msg") or body.get("errmsg") or str(body)
            except Exception:
                msg = r.text
            raise RuntimeError(f"HTTP {r.status_code}: {msg}")
        return r.json()



def _prepare_live_limit(symbol: str, month: str, lvl: str, title: str, amount: int) -> Tuple[bool, str]:
    """
    LIVE: создать реальный LIMIT BUY ордер на Binance.
    """
    symbol = (symbol or "").upper().strip()
    storage_dir = _storage_dir()
    # refresh free USDC (spot.free + Earn FLEX)
    try:
        free_trade = float(refresh_usdc_trade_free(storage_dir))
    except Exception:
        free_trade = float(get_usdc_spot_earn_total(storage_dir) or 0.0)

    if free_trade <= 0.0:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE отменён — нет свободного USDC (spot.free + Earn FLEX)."
        )
        return False, msg

    need = float(amount or 0)
    if need <= 0.0:
        msg = f"{symbol} {month}\n{title}: LIVE отменён — сумма ордера 0 USDC."
        return False, msg

    if need > free_trade + 1e-8:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE отменён — недостаточно свободного USDC. "
            f"Нужно ≥ {int(need)} USDC, доступно ~{int(free_trade)} USDC."
        )
        return False, msg

    sdata = _load_symbol_data(symbol)
    if not isinstance(sdata, dict):
        msg = f"{symbol} {month}\n{title}: LIVE отменён — нет данных по монете."
        return False, msg

    grid = sdata.get("grid") or {}
    try:
        price_lx = float(grid.get(lvl) or 0.0)
    except Exception:
        price_lx = 0.0
    if price_lx <= 0.0:
        msg = f"{symbol} {month}\n{title}: LIVE отменён — нет цены уровня {lvl}."
        return False, msg

    filters = sdata.get("filters") or {}
    try:
        tick = float(filters.get("tickSize")) if filters.get("tickSize") is not None else 0.0
    except Exception:
        tick = 0.0
    try:
        step = float(filters.get("stepSize")) if filters.get("stepSize") is not None else 0.0
    except Exception:
        step = 0.0
    try:
        min_qty = float(filters.get("minQty")) if filters.get("minQty") is not None else 0.0
    except Exception:
        min_qty = 0.0
    try:
        min_notional = float(filters.get("minNotional")) if filters.get("minNotional") is not None else 0.0
    except Exception:
        min_notional = 0.0

    # round price to tick
    if tick and tick > 0:
        price_lx = math.floor(price_lx / tick) * tick

    # quantity from amount in USDC
    qty_raw = need / price_lx if price_lx > 0 else 0.0
    qty = qty_raw
    if step and step > 0:
        qty = math.floor(qty_raw / step) * step
    qty = float(qty)
    notional = qty * price_lx

    if qty <= 0.0 or notional <= 0.0:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE отменён — после округления шагов объём ордера стал 0."
        )
        return False, msg

    if min_qty and qty + 1e-12 < min_qty:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE отменён — количество {qty:.8f} меньше минимального {min_qty:g}."
        )
        return False, msg

    if min_notional and notional + 1e-8 < min_notional:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE отменён — нотионал {notional:.6f} USDC меньше минимума {min_notional:g} USDC."
        )
        return False, msg

    key = os.getenv("BINANCE_API_KEY", "").strip()
    secret = os.getenv("BINANCE_API_SECRET", "").strip()
    if not key or not secret:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE невозможен — не заданы BINANCE_API_KEY / BINANCE_API_SECRET."
        )
        return False, msg

    # build clientOrderId
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    side = "BUY"
    client_order_id = f"{symbol}_{side}_{lvl}_{ts}"

    try:
        resp = _binance_limit_buy(symbol, price_lx, qty, key, secret, client_order_id=client_order_id)
    except Exception as e:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE ошибка Binance ({e.__class__.__name__}). Ордер не создан."
        )
        return False, msg

    # Extract identifiers for logging
    try:
        order_id = resp.get("orderId")
    except Exception:
        order_id = None
    try:
        client_id = resp.get("clientOrderId") or client_order_id
    except Exception:
        client_id = client_order_id
    status = resp.get("status", "NEW")

    # Log to state + logs
    record = {
        "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "side": side,
        "level": lvl,
        "amount_planned": int(amount),
        "price": price_lx,
        "qty": qty,
        "notional": notional,
        "orderId": order_id,
        "clientOrderId": client_id,
        "status": status,
        "orderType": "LIMIT",
    }
    try:
        state = _load_live_state()
        if symbol not in state or not isinstance(state.get(symbol), dict):
            state[symbol] = {}
        state[symbol][lvl] = record
        _save_live_state(state)
        _append_live_logs(record)
    except Exception:
        # логирование не должно ломать основной поток
        pass

    # success
    notional_str = f"{notional:.6f}"
    msg = (
        f"{symbol} {month}\n"
        f"{title}: LIVE LIMIT-ордер отправлен на биржу.\n"
        f"Сумма ≤ {int(need)} USDC, qty ≈ {qty:.8f}, нотионал ~{notional_str} USDC."
    )
    return True, msg


def _binance_market_buy(symbol: str, quote_amount: float, key: str, secret: str, client_order_id: str | None = None) -> dict:
    """
    Отправка SPOT MARKET BUY с quoteOrderQty.
    """
    params = {
        "symbol": symbol,
        "side": "BUY",
        "type": "MARKET",
        "quoteOrderQty": f"{quote_amount:.8f}".rstrip("0").rstrip("."),
        "recvWindow": 10_000,
        "timestamp": int(time.time() * 1000),
    }
    if client_order_id:
        params["newClientOrderId"] = client_order_id
    q = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sig = _sign_binance(q, secret)
    url = f"{BINANCE_API}/api/v3/order?{q}&signature={sig}"
    headers = {"X-MBX-APIKEY": key}
    with httpx.Client(timeout=10.0) as client:
        r = client.post(url, headers=headers)
        if r.status_code != 200:
            try:
                body = r.json()
                msg = body.get("msg") or body.get("errmsg") or str(body)
            except Exception:
                msg = r.text
            raise RuntimeError(f"HTTP {r.status_code}: {msg}")
        return r.json()


def _prepare_live_market(symbol: str, month: str, lvl: str, title: str, amount: int) -> Tuple[bool, str]:
    """
    LIVE: создать реальный MARKET BUY ордер на сумму USDC (quoteOrderQty).
    """
    symbol = (symbol or "").upper().strip()
    storage_dir = _storage_dir()
    # refresh free USDC (spot.free + Earn FLEX)
    try:
        free_trade = float(refresh_usdc_trade_free(storage_dir))
    except Exception:
        free_trade = float(get_usdc_spot_earn_total(storage_dir) or 0.0)

    if free_trade <= 0.0:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE отменён — нет свободного USDC (spot.free + Earn FLEX)."
        )
        return False, msg

    need = float(amount or 0)
    if need <= 0.0:
        msg = f"{symbol} {month}\n{title}: LIVE отменён — сумма ордера 0 USDC."
        return False, msg

    if need > free_trade + 1e-8:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE отменён — недостаточно свободного USDC. "
            f"Нужно ≥ {int(need)} USDC, доступно ~{int(free_trade)} USDC."
        )
        return False, msg

    sdata = _load_symbol_data(symbol)
    if not isinstance(sdata, dict):
        msg = f"{symbol} {month}\n{title}: LIVE отменён — нет данных по монете."
        return False, msg

    filters = sdata.get("filters") or {}
    try:
        min_notional = float(filters.get("minNotional")) if filters.get("minNotional") is not None else 0.0
    except Exception:
        min_notional = 0.0

    if min_notional and need + 1e-8 < min_notional:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE отменён — сумма {need:.2f} USDC меньше минимального нотионала {min_notional:g} USDC."
        )
        return False, msg

    key = os.getenv("BINANCE_API_KEY", "").strip()
    secret = os.getenv("BINANCE_API_SECRET", "").strip()
    if not key or not secret:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE невозможен — не заданы BINANCE_API_KEY / BINANCE_API_SECRET."
        )
        return False, msg

    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    side = "BUY"
    client_order_id = f"{symbol}_{side}_{lvl}_M_{ts}"

    try:
        resp = _binance_market_buy(symbol, need, key, secret, client_order_id=client_order_id)
    except Exception as e:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: LIVE ошибка Binance ({e.__class__.__name__}). Ордер не создан."
        )
        return False, msg

    # Попробуем вытащить примерные price/qty из ответа, если есть
    price = 0.0
    qty = 0.0
    notional = float(need)
    try:
        qty = float(resp.get("executedQty") or resp.get("origQty") or 0.0)
    except Exception:
        qty = 0.0
    try:
        cq = float(resp.get("cummulativeQuoteQty") or 0.0)
        if cq > 0 and qty > 0:
            price = cq / qty
            notional = cq
    except Exception:
        pass

    try:
        fills = resp.get("fills") or []
        if isinstance(fills, list) and fills and qty <= 0:
            total_q = 0.0
            total_n = 0.0
            for f in fills:
                try:
                    fq = float(f.get("qty") or 0.0)
                    fp = float(f.get("price") or 0.0)
                except Exception:
                    continue
                total_q += fq
                total_n += fq * fp
            if total_q > 0:
                qty = total_q
                price = total_n / total_q if total_n > 0 else price
                notional = total_n if total_n > 0 else notional
    except Exception:
        pass

    try:
        order_id = resp.get("orderId")
    except Exception:
        order_id = None
    try:
        client_id = resp.get("clientOrderId") or client_order_id
    except Exception:
        client_id = client_order_id
    status = resp.get("status", "NEW")

    record = {
        "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "side": side,
        "level": lvl,
        "amount_planned": int(amount),
        "price": price,
        "qty": qty,
        "notional": notional,
        "orderId": order_id,
        "clientOrderId": client_id,
        "status": status,
        "orderType": "MARKET",
    }
    try:
        state = _load_live_state()
        if symbol not in state or not isinstance(state.get(symbol), dict):
            state[symbol] = {}
        state[symbol][lvl] = record
        _save_live_state(state)
        _append_live_logs(record)
    except Exception:
        pass

    msg = (
        f"{symbol} {month}\n"
        f"{title}: LIVE MARKET-ордер отправлен на биржу.\n"
        f"Сумма ≈ {int(need)} USDC (quoteOrderQty), статус Binance: {status}."
    )
    return True, msg

def _symbol_data_path(symbol: str) -> str:
    return os.path.join(_storage_dir(), f"{symbol}.json")


def _load_symbol_data(symbol: str) -> dict:
    try:
        with open(_symbol_data_path(symbol), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_symbol_data(symbol: str, data: dict) -> None:
    """Безопасная запись JSON по монете (best-effort)."""
    path = _symbol_data_path(symbol)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        # best-effort: не ломаем бот из-за ошибок диска
        pass


def _recompute_symbol_flags(symbol: str) -> None:
    """Пересчитать автофлаги (включая ⚠️/✅) после изменения budget-levels.

    Используется после OPEN/CANCEL/FILL, чтобы карточка сразу показывала
    актуальные флаги, не ждя следующего прохода metrics_runner.
    """
    try:
        sdata = _load_symbol_data(symbol)
        if not isinstance(sdata, dict):
            return
        # trade_mode нужен, чтобы понять, что монета вообще торгуется
        mode = str(sdata.get("trade_mode") or "").upper()
        if mode != "LONG":
            # пока флаги считаем только для LONG-карточек
            pass
        sdata["flags"] = compute_all_flags(sdata)
        _save_symbol_data(symbol, sdata)
    except Exception:
        # не критично, просто не обновим флаги немедленно
        pass



def _compute_base_quota(symbol: str, month: str, lvl: str, budget: int) -> int:
    """Рассчитать базовую квоту по уровню на основе режима рынка и месячного бюджета."""
    if budget <= 0:
        return 0
    mode_key = _mode_key_from_symbol(symbol)
    perc = WEEKLY_PERCENT.get(mode_key, WEEKLY_PERCENT["RANGE"])
    try:
        p = int(perc.get(lvl) or 0)
    except Exception:
        p = 0
    if p <= 0:
        return 0
    quota = int(round(budget * p / 100.0))
    if quota < 0:
        quota = 0
    return quota


def _mode_key_from_symbol(symbol: str) -> str:
    sdata = _load_symbol_data(symbol)
    market_mode = sdata.get("market_mode")
    raw_mode = market_mode.get("12h") if isinstance(market_mode, dict) else market_mode
    raw_mode_str = str(raw_mode or "").upper()
    if "UP" in raw_mode_str:
        return "UP"
    elif "DOWN" in raw_mode_str:
        return "DOWN"
    return "RANGE"

def _flag_desc(flag: str) -> str:
    if flag == "🟢":
        return "цена ниже / внизу коридора — можно брать по рынку"
    if flag == "🟡":
        return "можно открыть по рекомендациям"
    if flag == "🔴":
        return "цена высока — ордер ставить рискованно"
    return "нет автофлага"





# ==== LIVE funds ensure (Earn -> Spot) =====================================

def _append_transfer_logs(record: Dict[str, Any]) -> None:
    try:
        storage = _storage_dir()
        # JSONL
        jpath = os.path.join(storage, "live_transfers_log.jsonl")
        with open(jpath, "a", encoding="utf-8") as jf:
            jf.write(json.dumps(record, ensure_ascii=False) + "\n")
        # CSV
        cpath = os.path.join(storage, "live_transfers_log.csv")
        if not os.path.exists(cpath):
            header = "ts,asset,direction,amount,status,requestId,note\n"
            with open(cpath, "w", encoding="utf-8") as cf:
                cf.write(header)
        line = f"{record.get('ts')},{record.get('asset')},{record.get('direction')},{record.get('amount')},{record.get('status')},{record.get('requestId')},{record.get('note')}\n"
        with open(cpath, "a", encoding="utf-8") as cf:
            cf.write(line)
    except Exception:
        pass


def _tg_info(msg: str) -> None:
    try:
        tg_send(msg)
    except Exception:
        # silent
        pass


def _get_usdc_balances() -> Tuple[float, float]:
    """Return (spot_free, earn_flexible) for USDC from portfolio storage.
    Falls back to 0,0 on any error."""
    try:
        storage_dir = os.getenv("STORAGE_DIR", "/data")
        # try to refresh the cached values (uses real API keys if present)
        try:
            refresh_usdc_trade_free(storage_dir)
        except Exception:
            pass
        path = os.path.join(storage_dir, "portfolio.json")
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f) or {}
        spot = float(state.get("usdc_spot_free") or state.get("spot_free") or 0.0)
        flex = float(state.get("usdc_earn_flex") or state.get("earn_flex") or 0.0)
        return spot, flex
    except Exception:
        return 0.0, 0.0

def _ensure_spot_usdc(amount_needed: float, buffer: float = 0.05, timeout_sec: float = 8.0) -> Tuple[bool, str]:
    """Ensure there is enough USDC on SPOT. If not, redeem from EARN flexible.
    Returns (ok, note). Sends sequential TG messages in monospace style."""
    need = max(0.0, float(amount_needed))
    if need <= 0:
        return True, "need<=0"
    spot, flex = _get_usdc_balances()
    if spot >= need:
        return True, "enough spot"
    deficit = round(need - spot + max(buffer, 0.001 * need), 2)
    if deficit <= 0:
        return True, "covered by buffer"
    if flex <= 0.0:
        return False, f"EARN empty (spot={spot:.4f}, earn={flex:.4f}, need={need:.4f})"

    # 1) notify
    _tg_info(f"```\nUSDC: недостаточно средств на SPOT\nПеревожу с EARN → SPOT: {deficit:.2f} USDC...\n```")

    # 2) request redeem (fast)
    req_id = ""
    rec = {
        "ts": int(time.time()),
        "asset": "USDC",
        "direction": "EARN_TO_SPOT",
        "amount": deficit,
        "status": "REQUESTED",
        "requestId": "",
        "note": "",
    }
    try:
        ok, data = binance_redeem_flexible("USDC", deficit)  # expects (ok, payload/requestId)
        if ok:
            req_id = str(data.get("requestId") if isinstance(data, dict) else data or "")
            rec["status"] = "CONFIRMING"
            rec["requestId"] = req_id
        else:
            rec["status"] = "ERROR"
            rec["note"] = str(data)
            _append_transfer_logs(rec)
            _tg_info("```\nUSDC: ошибка при переводе с EARN → SPOT\nОперация отменена\n```")
            return False, f"redeem error: {str(data)[:200]}"
    except Exception as e:
        rec["status"] = "ERROR"
        rec["note"] = f"exception: {e}"
        _append_transfer_logs(rec)
        _tg_info("```\nUSDC: ошибка при переводе с EARN → SPOT\nОперация отменена\n```")
        return False, "redeem exception"

    _append_transfer_logs(rec)

    # 3) wait for spot balance to increase
    deadline = time.time() + timeout_sec
    last_seen = spot
    while time.time() < deadline:
        time.sleep(0.4)
        s, _ = _get_usdc_balances()
        if s >= need:
            _tg_info(f"```\nUSDC: перевод с EARN подтверждён (+{deficit:.2f})\nОткрываю ордер...\n```")
            return True, "redeem ok"
        last_seen = s

    # timeout
    rec2 = rec.copy()
    rec2["status"] = "TIMEOUT"
    rec2["note"] = f"last_spot={last_seen}"
    _append_transfer_logs(rec2)
    _tg_info("```\nUSDC: перевод с EARN не подтвердился вовремя\nОперация отменена\n```")
    return False, "redeem timeout"

def _prepare_open_level(symbol: str, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)

    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен (Wk{week}) или бюджет 0 — {title} недоступен.", {}

    # базовая квота по режиму рынка
    base_quota = _compute_base_quota(symbol, month, lvl, budget)
    if base_quota <= 0:
        mode_key = _mode_key_from_symbol(symbol)
        return (
            f"{symbol} {month}\n"
            f"Для уровня {title} в режиме {mode_key} доля бюджета 0% — {title} недоступен.",
            {}
        )

    # уровни и текущий расход/резерв по Lx
    levels = get_pair_levels(symbol, month) or {}
    lvl_state = levels.get(lvl) or {}
    try:
        week_quota = int(lvl_state.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    quota = week_quota if week_quota > 0 else base_quota

    reserved = int(lvl_state.get("reserved") or 0)
    spent = int(lvl_state.get("spent") or 0)
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1
    used = reserved + (spent if last_fill_week == week else 0)
    available = quota - used
    if available <= 0:
        return f"{symbol} {month}\nЛимит по {title} уже исчерпан (доступно 0 USDC).", {}
    if free <= 0:
        return f"{symbol} {month}\nСвободный бюджет 0 USDC — сначала освободите бюджет.", {}

    if available > free:
        return (
            f"{symbol} {month}\n"
            f"По уровню {title} доступно {available} USDC, но свободно в бюджете только {free} USDC.\n"
            f"Сначала освободите бюджет или уменьшите другие уровни.",
            {}
        )

    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    flag_val = flags.get(lvl) or "-"
    flag_desc = _flag_desc(flag_val)

    mon_disp = month
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"

    # Если автофлаг 🔴 по L1 — сразу блокируем открытие до подтверждения
    if lvl == "L1" and flag_val == "🔴":
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} недоступен: автофлаг {flag_val} ({flag_desc})."
        )
        kb = {
            "inline_keyboard": [[
                {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
            ]]
        }
        return msg, kb

    # --- Подготовка данных для отображения подтверждения ---
    base = symbol.replace("USDC", "").replace("USDT", "")
    grid = sdata.get("grid") or {}
    try:
        price_lx = float(grid.get(lvl) or 0.0)
    except Exception:
        price_lx = 0.0

    price_info = sdata.get("price")
    last_price = 0.0
    try:
        if isinstance(price_info, dict):
            last_price = float(price_info.get("last") or 0.0)
        elif isinstance(price_info, (int, float)):
            last_price = float(price_info)
    except Exception:
        last_price = 0.0

    filters = sdata.get("filters") or {}
    try:
        tick = float(filters.get("tickSize")) if filters.get("tickSize") is not None else 0.0
    except Exception:
        tick = 0.0
    try:
        step = float(filters.get("stepSize")) if filters.get("stepSize") is not None else 0.0
    except Exception:
        step = 0.0

    # qty и нотионал при лимитной цене уровня (для оценки)
    qty = None
    if price_lx and price_lx > 0:
        qty_raw = float(available) / float(price_lx)
        if step and step > 0:
            qty = math.floor(qty_raw / step) * step
        else:
            qty = qty_raw
    notional = (qty or 0) * (price_lx or 0)

    # Процентное отклонение от текущей цены
    pct = None
    if last_price and price_lx:
        try:
            pct = ((price_lx - last_price) / last_price) * 100.0
        except Exception:
            pct = None
    pct_str = f"{pct:.2f}%" if isinstance(pct, float) else "-"
    tick_str = (f"{tick:g}" if tick else "-")
    step_str = (f"{step:g}" if step else "-")
    qty_str = (f"{qty:.8f}".rstrip("0").rstrip(".") if isinstance(qty, float) else "-")
    last_str = (f"{last_price:.2f}" if isinstance(last_price, float) else "-")
    price_str = (f"{price_lx:.2f}" if isinstance(price_lx, float) else "-")
    notional_str = (f"{notional:.6f}" if isinstance(notional, float) else "-")

    # Сообщение для LIMIT (🟡 и прочие)
    msg_limit = (
        f"{symbol} {mon_disp} Wk{week}\n"
        f"{title} • SPOT LIMIT BUY (GTC)\n\n"
        f"Цена (L{lvl[-1]}): {price_str} USDC  (tick {tick_str})\n"
        f"Текущая:   {last_str} USDC  (Δ {pct_str})\n\n"
        f"Сумма: {available} USDC  →  Qty: {qty_str} {base}  (step {step_str})\n"
        f"Нотионал: {notional_str} USDC"
    )

    # Сообщение для MARKET (🟢 по L1)
    if lvl == "L1" and flag_val == "🟢":
        est_qty_str = qty_str  # оценка по уровню, достаточно для предварительного вида
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} • SPOT MARKET BUY\n\n"
            f"Цена (L1): {price_str} USDC  (tick {tick_str})\n"
            f"Текущая:   {last_str} USDC  (Δ {pct_str})\n\n"
            f"Сумма: {available} USDC  →  исполнение по рынку ~ Qty: {est_qty_str} {base}  (step {step_str})"
        )
    else:
        msg = msg_limit

    cb = f"ORDERS_OPEN_{lvl}_CONFIRM"
    kb = {
        "inline_keyboard": [[
            {"text": "CONFIRM", "callback_data": f"{cb}:{symbol}:{available}"},
            {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
        ]]
    }
    return msg, kb

def _confirm_open_level(symbol: str, amount: int, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    symbol = (symbol or "").upper().strip()
    if not symbol or int(amount) <= 0:
        return "Некорректные параметры операции.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)

    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл не запущен или бюджет 0 — операция отменена.", {}

    base_quota = _compute_base_quota(symbol, month, lvl, budget)
    if base_quota <= 0:
        mode_key = _mode_key_from_symbol(symbol)
        return (
            f"{symbol} {month}\n"
            f"Для уровня {title} в режиме {mode_key} доля бюджета 0% — операция отменена.",
            {}
        )

    levels = get_pair_levels(symbol, month) or {}
    lvl_state = levels.get(lvl) or {}
    try:
        week_quota = int(lvl_state.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    quota = week_quota if week_quota > 0 else base_quota

    reserved = int(lvl_state.get("reserved") or 0)
    spent = int(lvl_state.get("spent") or 0)
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1
    used = reserved + (spent if last_fill_week == week else 0)
    available = quota - used
    if available <= 0 or free <= 0:
        return f"{symbol} {month}\nЛимит по {title} или свободный бюджет уже исчерпаны — операция отменена.", {}

    actual = min(int(amount), available, free)
    if actual <= 0:
        return f"{symbol} {month}\nФактическая доступная сумма 0 USDC — операция отменена.", {}

    # Определяем актуальный автофлаг для безопасности
    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    flag_val = flags.get(lvl) or "-"

    # Если к моменту подтверждения уровень стал 🔴 — полностью блокируем операцию
    if lvl == "L1" and flag_val == "🔴":
        return (
            f"{symbol} {month}\n"
            f"{title}: автофлаг {flag_val} — открытие уровня сейчас заблокировано.",
            {}
        )

    # LIVE-ветка: для live-пары выбираем тип ордера по флагу
    if lvl == "L1" and _is_live_pair(symbol):
        # Обязательная проверка наличия средств на SPOT (с возможным redeem с EARN)
        ok_funds, note_funds = _ensure_spot_usdc(float(actual))
        if not ok_funds:
            return note_funds, {}

        if flag_val == "🟢":
            ok, live_msg = _prepare_live_market(symbol, month, lvl, title, actual)
        else:
            ok, live_msg = _prepare_live_limit(symbol, month, lvl, title, actual)
        if not ok:
            # Ошибка LIVE — бюджет/резервы не трогаем, просто возвращаем сообщение
            return live_msg, {}
        # Если LIVE прошёл успешно — продолжаем обновлять виртуальные резервы как обычно

    new_reserved = int(lvl_state.get("reserved") or 0) + actual
    new_spent = int(lvl_state.get("spent") or 0)
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1

    levels[lvl] = {
        "reserved": new_reserved,
        "spent": new_spent,
        "week_quota": week_quota if week_quota > 0 else quota,
        "last_fill_week": last_fill_week,
    }
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    # После изменения резервов обновляем автофлаги (включая ⚠️/✅).
    _recompute_symbol_flags(symbol)

    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {"inline_keyboard": [
            [
                {"text": "OCO", "callback_data": f"ORDERS_OPEN_OCO:{sym}"},
                {"text": "LIMIT 0", "callback_data": f"ORDERS_OPEN_L0:{sym}"},
                {"text": "LIMIT 1", "callback_data": f"ORDERS_OPEN_L1:{sym}"},
                {"text": "LIMIT 2", "callback_data": f"ORDERS_OPEN_L2:{sym}"},
                {"text": "LIMIT 3", "callback_data": f"ORDERS_OPEN_L3:{sym}"},
            ],
            [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
            ],
        ]}
        return card, kb
    except Exception:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: ордер на {actual} USDC учтён в резерве.\n"
            f"Бюджет: {info2.get('budget')} | "
            f"⏳ {info2.get('reserve')} | "
            f"💸 {info2.get('spent')} | "
            f"🎯 {info2.get('free')}"
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_OPEN_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_OPEN_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_OPEN_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_OPEN_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_OPEN_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb
def _prepare_cancel_level(symbol: str, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка отмены виртуального ордера: показ суммы в резерве и подтверждение."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    week = int(info.get("week") or 0)

    levels = get_pair_levels(symbol, month)
    lvl_state = levels.get(lvl) or {}
    reserved = int(lvl_state.get("reserved") or 0)

    mon_disp = month
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"

    if reserved <= 0:
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} CANCEL\n\n"
            f"Нет виртуального ордера на уровне {title} (в резерве 0 USDC)."
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb

    msg = (
        f"{symbol} {mon_disp} Wk{week}\n"
        f"{title} CANCEL\n\n"
        f"Сейчас в резерве: {reserved} USDC\n"
        f"Вернуть в free:   {reserved} USDC\n\n"
        f"Отменить виртуальный {title} на {reserved} USDC?"
    )
    cb = f"ORDERS_CANCEL_{lvl}_CONFIRM"
    kb = {
        "inline_keyboard": [[
            {"text": "CONFIRM", "callback_data": f"{cb}:{symbol}:{reserved}"},
            {"text": "↩️", "callback_data": f"ORDERS_CANCEL:{symbol}"},
        ]]
    }
    return msg, kb



def _confirm_cancel_level(symbol: str, amount: int, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подтверждение отмены: возвращаем резерв в free."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректные параметры операции.", {}

    month = datetime.now().strftime("%Y-%m")
    levels = get_pair_levels(symbol, month)
    lvl_state = levels.get(lvl) or {}
    reserved = int(lvl_state.get("reserved") or 0)

    if reserved <= 0:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        msg = (
            f"{symbol} {mon_disp} Wk?\n"
            f"{title} CANCEL\n\n"
            f"Нечего отменять: резерв уже 0 USDC."
        )
        sym = symbol
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{sym}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{sym}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{sym}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{sym}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{sym}"},
                ],
                [
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return msg, kb

    try:
        requested = int(amount)
    except Exception:
        requested = 0
    if requested <= 0:
        requested = reserved
    actual = min(reserved, requested)
    new_reserved = reserved - actual
    if new_reserved < 0:
        new_reserved = 0

    # сохраняем только резерв, остальные поля (spent/week_quota/last_fill_week) не трогаем
    try:
        spent = int(lvl_state.get("spent") or 0)
    except Exception:
        spent = 0
    try:
        week_quota = int(lvl_state.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1

    levels[lvl] = {
        "reserved": new_reserved,
        "spent": spent,
        "week_quota": week_quota,
        "last_fill_week": last_fill_week,
    }
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    # После изменения резервов обновляем автофлаги (⚠️/✅/авто).
    _recompute_symbol_flags(symbol)

    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{sym}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{sym}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{sym}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{sym}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{sym}"},
                ],
                [
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        msg = (
            f"{symbol} {mon_disp}\n"
            f"{title}: отменён виртуальный ордер на {actual} USDC.\n"
            f"Бюджет: {info2.get('budget')} | "
            f"⏳ {info2.get('reserve')} | "
            f"💸 {info2.get('spent')} | "
            f"🎯 {info2.get('free')}"
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb



# Публичные API для уровней

# Публичные API для уровней
def prepare_open_oco(symbol: str):  return _prepare_open_level(symbol, "OCO", "OCO")
def confirm_open_oco(symbol: str, amount: int):  return _confirm_open_level(symbol, amount, "OCO", "OCO")

def prepare_open_l0(symbol: str):   return _prepare_open_level(symbol, "L0", "LIMIT 0")
def confirm_open_l0(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L0", "LIMIT 0")

def prepare_open_l1(symbol: str):   return _prepare_open_level(symbol, "L1", "LIMIT 1")
def confirm_open_l1(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L1", "LIMIT 1")

def prepare_open_l2(symbol: str):   return _prepare_open_level(symbol, "L2", "LIMIT 2")
def confirm_open_l2(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L2", "LIMIT 2")

def prepare_open_l3(symbol: str):   return _prepare_open_level(symbol, "L3", "LIMIT 3")
def confirm_open_l3(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L3", "LIMIT 3")

def prepare_cancel_oco(symbol: str):  return _prepare_cancel_level(symbol, "OCO", "OCO")
def confirm_cancel_oco(symbol: str, amount: int):  return _confirm_cancel_level(symbol, amount, "OCO", "OCO")

def prepare_cancel_l0(symbol: str):   return _prepare_cancel_level(symbol, "L0", "LIMIT 0")
def confirm_cancel_l0(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L0", "LIMIT 0")

def prepare_cancel_l1(symbol: str):   return _prepare_cancel_level(symbol, "L1", "LIMIT 1")
def confirm_cancel_l1(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L1", "LIMIT 1")

def prepare_cancel_l2(symbol: str):   return _prepare_cancel_level(symbol, "L2", "LIMIT 2")
def confirm_cancel_l2(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L2", "LIMIT 2")

def prepare_cancel_l3(symbol: str):   return _prepare_cancel_level(symbol, "L3", "LIMIT 3")
def confirm_cancel_l3(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L3", "LIMIT 3")


def recompute_flags_for_symbol(symbol: str) -> None:
    """Публичный помощник для пересчёта флагов по монете."""
    _recompute_symbol_flags(symbol)


def _prepare_fill_level(symbol: str, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка пометки уровня как исполненного (FILL)."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    week = int(info.get("week") or 0)

    levels = get_pair_levels(symbol, month)
    lvl_state = levels.get(lvl) or {}
    reserved = int(lvl_state.get("reserved") or 0)

    mon_disp = month
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"

    if week <= 0:
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} FILL\n\n"
            f"Цикл ещё не запущен — пометка исполнения недоступна."
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb

    if reserved <= 0:
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} FILL\n\n"
            f"Нет открытого виртуального ордера на уровне {title} (в резерве 0 USDC)."
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_FILL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_FILL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_FILL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_FILL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_FILL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb

    msg = (
        f"{symbol} {mon_disp} Wk{week}\n"
        f"{title} FILL\n\n"
        f"Сейчас в резерве: {reserved} USDC\n"
        f"Перевести в spent: {reserved} USDC?\n\n"
        f"Пометить виртуальный {title} как полностью исполненный?"
    )
    cb = f"ORDERS_FILL_{lvl}_CONFIRM"
    kb = {
        "inline_keyboard": [[
            {"text": "CONFIRM", "callback_data": f"{cb}:{symbol}:{reserved}"},
            {"text": "↩️", "callback_data": f"ORDERS_FILL:{symbol}"},
        ]]
    }
    return msg, kb


def _confirm_fill_level(symbol: str, amount: int, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подтверждение FILL: переводим резерв в spent."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректные параметры операции.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    week = int(info.get("week") or 0)

    levels = get_pair_levels(symbol, month)
    lvl_state = levels.get(lvl) or {}
    reserved = int(lvl_state.get("reserved") or 0)
    try:
        spent = int(lvl_state.get("spent") or 0)
    except Exception:
        spent = 0
    try:
        week_quota = int(lvl_state.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1

    if reserved <= 0:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} FILL\n\n"
            f"Нечего помечать: резерв уже 0 USDC."
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_FILL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_FILL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_FILL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_FILL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_FILL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb

    try:
        requested = int(amount)
    except Exception:
        requested = 0
    if requested <= 0:
        requested = reserved
    actual = min(reserved, requested)
    new_reserved = reserved - actual
    if new_reserved < 0:
        new_reserved = 0
    new_spent = spent + actual

    # помечаем, что исполнение было в текущую неделю
    if actual > 0 and week > 0:
        last_fill_week = week

    levels[lvl] = {
        "reserved": new_reserved,
        "spent": new_spent,
        "week_quota": week_quota,
        "last_fill_week": last_fill_week,
    }
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    # После изменения резервов обновляем автофлаги (⚠️/✅/авто).
    _recompute_symbol_flags(symbol)

    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_FILL_OCO:{sym}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_FILL_L0:{sym}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_FILL_L1:{sym}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_FILL_L2:{sym}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_FILL_L3:{sym}"},
                ],
                [
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        msg = (
            f"{symbol} {mon_disp}\n"
            f"{title}: помечен как исполненный на {actual} USDC.\n"
            f"Бюджет: {info2.get('budget')} | "
            f"⏳ {info2.get('reserve')} | "
            f"💸 {info2.get('spent')} | "
            f"🎯 {info2.get('free')}"
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_FILL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_FILL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_FILL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_FILL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_FILL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb


def perform_rollover(symbol: str) -> Dict[str, Any]:
    """Роловер недели: снять виртуальные ордера, перерасчитать недельные квоты и увеличить week."""

    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    week = int(info.get("week") or 0)

    if budget <= 0 or week <= 0:
        # цикл не запущен
        return info

    # читаем уровни
    levels = get_pair_levels(symbol, month) or {}

    for lvl in LEVEL_KEYS:
        st = levels.get(lvl) or {}
        try:
            reserved = int(st.get("reserved") or 0)
        except Exception:
            reserved = 0
        try:
            spent = int(st.get("spent") or 0)
        except Exception:
            spent = 0
        try:
            week_quota = int(st.get("week_quota") or 0)
        except Exception:
            week_quota = 0
        try:
            last_fill_week = int(st.get("last_fill_week") if st.get("last_fill_week") is not None else -1)
        except Exception:
            last_fill_week = -1

        # базовая квота на следующую неделю
        base = _compute_base_quota(symbol, month, lvl, budget)

        had_fill = (last_fill_week == week)
        if had_fill:
            next_week_quota = base
        else:
            quota_prev = week_quota if week_quota > 0 else base
            next_week_quota = base + quota_prev
            if base > 0:
                max_quota = 4 * base
                if next_week_quota > max_quota:
                    next_week_quota = max_quota

        if next_week_quota < 0:
            next_week_quota = 0

        levels[lvl] = {
            "reserved": 0,  # все ордера снимаем → деньги вернутся в free
            "spent": spent,
            "week_quota": next_week_quota,
            "last_fill_week": -1,  # новая неделя — ещё не исполнялось
        }

    # сохраняем уровни и пересчитываем агрегаты
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    # ensure week increment and fresh state
    info3 = info2
    try:
        new_week = week + 1
        set_pair_week(symbol, month, new_week)
        info3 = get_pair_budget(symbol, month)
    except Exception:
        # fallback: return aggregates before week increment if anything fails
        pass
# после ролловера пересчитаем флаги
    _recompute_symbol_flags(symbol)

    return info3


# -------------------------
# OPEN ALL helpers

def _calc_available_for_level(symbol: str, month: str, week: int, lvl: str, budget: int) -> int:
    """Доступная сумма к открытию по уровню с учётом квот и already used/filled этой недели."""
    levels = get_pair_levels(symbol, month) or {}
    base_quota = _compute_base_quota(symbol, month, lvl, budget)
    if base_quota <= 0:
        return 0
    st = levels.get(lvl) or {}
    try:
        week_quota = int(st.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    quota = week_quota if week_quota > 0 else base_quota
    try:
        last_fill_week = int(st.get("last_fill_week") if st.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1
    reserved = int(st.get("reserved") or 0)
    spent_curr = int(st.get("spent") or 0) if last_fill_week == week else 0
    available = quota - (reserved + spent_curr)
    return available if available > 0 else 0


def prepare_open_all_limit(symbol: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка: открыть все лимитные уровни (🟡).
    Если свободных средств меньше общей суммы — предупреждаем и предлагаем
    открыть только ПОЛНЫЕ квоты сверху вниз (без частичных).
    """
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — ALL недоступен.", {}

    # собираем список уровней со статусом 🟡 (включая OCO) в порядке сверху-вниз
    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    yellow = {k for k,v in (flags or {}).items() if v == "🟡"}
    levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in yellow]

    # базовый план: для каждого уровня доступное «a» к открытию
    items: list[tuple[str,int]] = []
    total = 0
    for lvl in levels_list:
        a = _calc_available_for_level(symbol, month, week, lvl, budget)
        if a > 0:
            items.append((lvl, a))
            total += a

    if total <= 0:
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"}]]}
        return f"{symbol} {month}\nALL (лимит) — нечего открывать.", kb

    mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month

    if free >= total:
        # хватает на всё — обычное подтверждение
        parts = ", ".join([f"{lvl} {amt}" for lvl,amt in items])
        msg = (f"{symbol} {mon_disp} Wk{week}\n⚠️ ALL (лимит)\n\n"
               f"Открыть {len(items)} ордера на сумму {total} USDC?\nСписок: {parts}")
        kb = {"inline_keyboard":[
            [{"text":"CONFIRM","callback_data":f"ORDERS_OPEN_ALL_LIMIT_CONFIRM:{symbol}"}],
            [{"text":"MANUAL","callback_data":f"ORDERS_OPEN:{symbol}"}],
        ]}
        # сохраним план в оперативке
        try:
            _RUNTIME_PLANS[(symbol, month, "limit_all_full")] = items.copy()
        except Exception:
            pass
        return msg, kb

    # Не хватает средств — предложим открыть ПОЛНЫЕ квоты сверху вниз
    selected: list[tuple[str,int]] = []
    sel_sum = 0
    for lvl, a in items:
        if sel_sum + a <= free:
            selected.append((lvl, a))
            sel_sum += a
        else:
            continue

    if not selected:
        msg = (f"{symbol} {mon_disp} Wk{week}\n⚠️ ALL (лимит)\n\n"
               f"Доступно: {free} USDC, нужно: {total}. Недостаточно средств для любых уровней.\n"
               f"Откройте по одному или пополните баланс.")
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_OPEN:{symbol}"}]]}
        return msg, kb

    plan = ", ".join(f"{k} {q}" for k,q in items)
    will = ", ".join(f"{k} {q}" for k,q in selected)
    miss_items = [(k,q) for k,q in items if (k,q) not in selected]
    miss = ", ".join(f"{k} {q}" for k,q in miss_items) if miss_items else "—"
    msg = (f"{symbol} {mon_disp} Wk{week}\n⚠️ ALL (лимит)\n\n"
           f"Доступно: {free} USDC, нужно: {total} (не хватает {total-free}).\n"
           f"Открыть ПОЛНЫЕ квоты сверху вниз, без частичных?\n\n"
           f"План: {plan}\nБудет открыто: {will}\nПропущены: {miss}")
    kb = {"inline_keyboard":[
        [{"text":"CONFIRM","callback_data":f"ORDERS_OPEN_ALL_LIMIT_CONFIRM:{symbol}"}],
        [{"text":"MANUAL","callback_data":f"ORDERS_OPEN:{symbol}"}],
    ]}
    try:
        _RUNTIME_PLANS[(symbol, month, "limit_all_full")] = selected.copy()
    except Exception:
        pass
    return msg, kb

def confirm_open_all_limit(symbol: str) -> Tuple[str, Dict[str, Any]]:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — операция отменена.", {}

    # загрузим сохранённый план (если есть), иначе сформируем по текущим 🟡
    plan = _RUNTIME_PLANS.pop((symbol, month, "limit_all_full"), None)
    if plan is None:
        sdata = _load_symbol_data(symbol)
        flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
        yellow = {k for k,v in (flags or {}).items() if v == "🟡"}
        levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in yellow]
        plan = []
        for lvl in levels_list:
            a = _calc_available_for_level(symbol, month, week, lvl, budget)
            if a > 0:
                plan.append((lvl, a))

    levels = get_pair_levels(symbol, month) or {}
    applied: list[tuple[str,int]] = []
    total = 0

    for lvl, a in plan:
        if a <= 0:
            continue
        if free < a:
            # без частичных
            continue
        st = levels.get(lvl) or {}
        reserved = int(st.get("reserved") or 0)
        spent = int(st.get("spent") or 0)
        week_quota = int(st.get("week_quota") or 0)
        last_fill_week = int(st.get("last_fill_week") if st.get("last_fill_week") is not None else -1)
        levels[lvl] = {
            "reserved": reserved + a,
            "spent": spent,
            "week_quota": week_quota,
            "last_fill_week": last_fill_week,
        }
        free -= a
        total += a
        applied.append((lvl, a))

    save_pair_levels(symbol, month, levels)
    recompute_pair_aggregates(symbol, month)
    _recompute_symbol_flags(symbol)

    # Пересобираем карточку и остаёмся в OPEN
    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard":[
                [
                    {"text":"OCO","callback_data":f"ORDERS_OPEN_OCO:{sym}"},
                    {"text":"LIMIT 0","callback_data":f"ORDERS_OPEN_L0:{sym}"},
                    {"text":"LIMIT 1","callback_data":f"ORDERS_OPEN_L1:{sym}"},
                    {"text":"LIMIT 2","callback_data":f"ORDERS_OPEN_L2:{sym}"},
                    {"text":"LIMIT 3","callback_data":f"ORDERS_OPEN_L3:{sym}"},
                ],
                [
                    {"text":"✅ ALL","callback_data":f"ORDERS_OPEN_ALL_MKT:{sym}"},
                    {"text":"⚠️ ALL","callback_data":f"ORDERS_OPEN_ALL_LIMIT:{sym}"},
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                    {"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        # Фоллбек
        mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
        parts = ", ".join(f"{k} {q}" for k,q in applied) if applied else "—"
        return (f"{symbol} {mon_disp}\n⚠️ ALL выполнен. Открыто: {parts} на {total} USDC.",
                {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_OPEN:{symbol}"}]]})

def prepare_open_all_mkt(symbol: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка: маркет-исполнение (🟢) всех доступных уровней на их квоты."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — ALL недоступен.", {}

    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    green = {k for k,v in (flags or {}).items() if v == "🟢"}
    levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in green]

    items = []
    total = 0
    for lvl in levels_list:
        a = _calc_available_for_level(symbol, month, week, lvl, budget)
        if a > 0:
            items.append((lvl, a))
            total += a

    if total <= 0:
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"}]]}
        return f"{symbol} {month}\n✅ ALL — нечего исполнять.", kb

    mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
    parts = ", ".join([f"{lvl} {amt}" for lvl,amt in items])
    msg = (f"{symbol} {mon_disp} Wk{week}\n✅ ALL (маркет)\n\n"
           f"Исполнить {len(items)} ордеров на сумму {total} USDC?\nСписок: {parts}")
    kb = {"inline_keyboard":[
        [{"text":"CONFIRM","callback_data":f"ORDERS_OPEN_ALL_MKT_CONFIRM:{symbol}"}],
        [{"text":"CANCEL","callback_data":f"ORDERS_OPEN_ALL_MKT_CANCEL:{symbol}"}],
    ]}
    return msg, kb


def confirm_open_all_mkt(symbol: str) -> Tuple[str, Dict[str, Any]]:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — операция отменена.", {}

    levels = get_pair_levels(symbol, month) or {}
    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    green = {k for k,v in (flags or {}).items() if v == "🟢"}
    levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in green]

    applied = []
    total = 0
    for lvl in levels_list:
        a = _calc_available_for_level(symbol, month, week, lvl, budget)
        if a <= 0:
            continue
        st = levels.get(lvl) or {}
        reserved = int(st.get("reserved") or 0)
        try:
            spent = int(st.get("spent") or 0)
        except Exception:
            spent = 0
        try:
            week_quota = int(st.get("week_quota") or 0)
        except Exception:
            week_quota = 0
        # FILL: перевод в spent и фиксация недели
        levels[lvl] = {
            "reserved": reserved,
            "spent": spent + a,
            "week_quota": week_quota,
            "last_fill_week": week,
        }
        total += a
        applied.append((lvl, a))

    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)
    _recompute_symbol_flags(symbol)

    if total <= 0:
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"}]]}
        return f"{symbol} {month}\n✅ ALL — ничего не исполнено.", kb

    mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
    parts = ", ".join([f"{lvl} {amt}" for lvl,amt in applied])
    msg = (f"{symbol} {mon_disp} Wk{week}\n✅ ALL (маркет)\n\n"
           f"Исполнено {len(applied)} на сумму {total} USDC.\nСписок: {parts}")
    
    # После изменений пересобираем карточку и остаёмся в подменю OPEN
    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard":[
                [
                    {"text":"OCO","callback_data":f"ORDERS_OPEN_OCO:{sym}"},
                    {"text":"LIMIT 0","callback_data":f"ORDERS_OPEN_L0:{sym}"},
                    {"text":"LIMIT 1","callback_data":f"ORDERS_OPEN_L1:{sym}"},
                    {"text":"LIMIT 2","callback_data":f"ORDERS_OPEN_L2:{sym}"},
                    {"text":"LIMIT 3","callback_data":f"ORDERS_OPEN_L3:{sym}"},
                ],
                [
                    {"text":"✅ ALL","callback_data":f"ORDERS_OPEN_ALL_MKT:{sym}"},
                    {"text":"⚠️ ALL","callback_data":f"ORDERS_OPEN_ALL_LIMIT:{sym}"},
                    {"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        # Фоллбек: текстовое подтверждение, если сборка карточки упала
        mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
        return f"{symbol} {mon_disp}\nОперация выполнена.", kb

# -------------------------

# Публичные обёртки для FILL
def prepare_fill_oco(symbol: str):  return _prepare_fill_level(symbol, "OCO", "OCO")
def confirm_fill_oco(symbol: str, amount: int):  return _confirm_fill_level(symbol, amount, "OCO", "OCO")

def prepare_fill_l0(symbol: str):   return _prepare_fill_level(symbol, "L0", "LIMIT 0")
def confirm_fill_l0(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L0", "LIMIT 0")

def prepare_fill_l1(symbol: str):   return _prepare_fill_level(symbol, "L1", "LIMIT 1")
def confirm_fill_l1(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L1", "LIMIT 1")

def prepare_fill_l2(symbol: str):   return _prepare_fill_level(symbol, "L2", "LIMIT 2")
def confirm_fill_l2(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L2", "LIMIT 2")

def prepare_fill_l3(symbol: str):   return _prepare_fill_level(symbol, "L3", "LIMIT 3")
def confirm_fill_l3(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L3", "LIMIT 3")

def prepare_cancel_all(symbol: str):
    """Подготовка отмены всех открытых (⚠️ reserved>0) ордеров: OCO, L0–L3."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    mon_disp = month
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"
    levels = get_pair_levels(symbol, month)
    if not isinstance(levels, dict):
        levels = {}
    order_keys = ["OCO","L0","L1","L2","L3"]
    items = []
    total = 0
    for k in order_keys:
        st = levels.get(k) or {}
        r = int(st.get("reserved") or 0)
        if r > 0:
            items.append(f"{k} {r}")
            total += r
    if total <= 0:
        return (f"{symbol} {mon_disp}\n"
                f"❌ ALL — нечего отменять."), {
            "inline_keyboard":[
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{symbol}"},
                ],
                [
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{symbol}"},
                    {"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"},
                ]
            ]
        }
    msg = (f"{symbol} {mon_disp}\n"
           f"❌ ALL (cancel)\n\n"
           f"Отменить {len(items)} ордера на сумму {total} USDC?\n"
           f"Список: {', '.join(items)}")
    kb = {
        "inline_keyboard":[[
            {"text":"CONFIRM","callback_data":f"ORDERS_CANCEL_ALL_CONFIRM:{symbol}"},
            {"text":"↩️","callback_data":f"ORDERS_CANCEL:{symbol}"},
        ]]
    }
    return msg, kb


def confirm_cancel_all(symbol: str):
    """Отмена всех открытых (⚠️) ордеров — reserved→0, пересбор карточки и подменю CANCEL."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректные параметры операции.", {}
    month = datetime.now().strftime("%Y-%m")
    levels = get_pair_levels(symbol, month)
    if not isinstance(levels, dict):
        levels = {}
    changed = False
    total = 0
    for k in ["OCO","L0","L1","L2","L3"]:
        st = levels.get(k) or {}
        r = int(st.get("reserved") or 0)
        if r > 0:
            total += r
            changed = True
            levels[k] = {
                "reserved": 0,
                "spent": int(st.get("spent") or 0),
                "week_quota": int(st.get("week_quota") or 0),
                "last_fill_week": int(st.get("last_fill_week") or 0),
            }
    if not changed:
        # Нечего отменять — просто вернуть текущее подменю CANCEL
        try:
            card = build_symbol_message(symbol)
            sym = (symbol or "").upper()
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{sym}"},
                        {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{sym}"},
                        {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{sym}"},
                        {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{sym}"},
                        {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{sym}"},
                    ],
                    [
                        {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                        {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                    ],
                ]
            }
            return card, kb
        except Exception:
            return "❌ ALL — нечего отменять.", {}
    # Сохраняем и пересчитываем агрегаты/флаги
    save_pair_levels(symbol, month, levels)
    recompute_pair_aggregates(symbol, month)
    _recompute_symbol_flags(symbol)
    # Пересобираем карточку и остаёмся в CANCEL
    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{sym}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{sym}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{sym}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{sym}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{sym}"},
                ],
                [
                        {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                        {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                    ],
            ]
        }
        return card, kb
    except Exception:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        return f"{symbol} {mon_disp}\nОтменено на сумму {total} USDC.", {}

def _prepare_cancel_level(symbol: str, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка отмены виртуального ордера: показ суммы в резерве и подтверждение."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    week = int(info.get("week") or 0)

    levels = get_pair_levels(symbol, month)
    lvl_state = levels.get(lvl) or {}
    reserved = int(lvl_state.get("reserved") or 0)

    mon_disp = month
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"

    if reserved <= 0:
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} CANCEL\n\n"
            f"Нет виртуального ордера на уровне {title} (в резерве 0 USDC)."
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb

    msg = (
        f"{symbol} {mon_disp} Wk{week}\n"
        f"{title} CANCEL\n\n"
        f"Сейчас в резерве: {reserved} USDC\n"
        f"Вернуть в free:   {reserved} USDC\n\n"
        f"Отменить виртуальный {title} на {reserved} USDC?"
    )
    cb = f"ORDERS_CANCEL_{lvl}_CONFIRM"
    kb = {
        "inline_keyboard": [[
            {"text": "CONFIRM", "callback_data": f"{cb}:{symbol}:{reserved}"},
            {"text": "↩️", "callback_data": f"ORDERS_CANCEL:{symbol}"},
        ]]
    }
    return msg, kb



def _confirm_cancel_level(symbol: str, amount: int, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подтверждение отмены: возвращаем резерв в free."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректные параметры операции.", {}

    month = datetime.now().strftime("%Y-%m")
    levels = get_pair_levels(symbol, month)
    lvl_state = levels.get(lvl) or {}
    reserved = int(lvl_state.get("reserved") or 0)

    if reserved <= 0:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        msg = (
            f"{symbol} {mon_disp} Wk?\n"
            f"{title} CANCEL\n\n"
            f"Нечего отменять: резерв уже 0 USDC."
        )
        sym = symbol
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{sym}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{sym}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{sym}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{sym}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{sym}"},
                ],
                [
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return msg, kb

    try:
        requested = int(amount)
    except Exception:
        requested = 0
    if requested <= 0:
        requested = reserved
    actual = min(reserved, requested)
    new_reserved = reserved - actual
    if new_reserved < 0:
        new_reserved = 0

    # сохраняем только резерв, остальные поля (spent/week_quota/last_fill_week) не трогаем
    try:
        spent = int(lvl_state.get("spent") or 0)
    except Exception:
        spent = 0
    try:
        week_quota = int(lvl_state.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1

    levels[lvl] = {
        "reserved": new_reserved,
        "spent": spent,
        "week_quota": week_quota,
        "last_fill_week": last_fill_week,
    }
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    # После изменения резервов обновляем автофлаги (⚠️/✅/авто).
    _recompute_symbol_flags(symbol)

    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{sym}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{sym}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{sym}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{sym}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{sym}"},
                ],
                [
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        msg = (
            f"{symbol} {mon_disp}\n"
            f"{title}: отменён виртуальный ордер на {actual} USDC.\n"
            f"Бюджет: {info2.get('budget')} | "
            f"⏳ {info2.get('reserve')} | "
            f"💸 {info2.get('spent')} | "
            f"🎯 {info2.get('free')}"
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb



# Публичные API для уровней

# Публичные API для уровней
def prepare_open_oco(symbol: str):  return _prepare_open_level(symbol, "OCO", "OCO")
def confirm_open_oco(symbol: str, amount: int):  return _confirm_open_level(symbol, amount, "OCO", "OCO")

def prepare_open_l0(symbol: str):   return _prepare_open_level(symbol, "L0", "LIMIT 0")
def confirm_open_l0(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L0", "LIMIT 0")

def prepare_open_l1(symbol: str):   return _prepare_open_level(symbol, "L1", "LIMIT 1")
def confirm_open_l1(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L1", "LIMIT 1")

def prepare_open_l2(symbol: str):   return _prepare_open_level(symbol, "L2", "LIMIT 2")
def confirm_open_l2(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L2", "LIMIT 2")

def prepare_open_l3(symbol: str):   return _prepare_open_level(symbol, "L3", "LIMIT 3")
def confirm_open_l3(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L3", "LIMIT 3")

def prepare_cancel_oco(symbol: str):  return _prepare_cancel_level(symbol, "OCO", "OCO")
def confirm_cancel_oco(symbol: str, amount: int):  return _confirm_cancel_level(symbol, amount, "OCO", "OCO")

def prepare_cancel_l0(symbol: str):   return _prepare_cancel_level(symbol, "L0", "LIMIT 0")
def confirm_cancel_l0(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L0", "LIMIT 0")

def prepare_cancel_l1(symbol: str):   return _prepare_cancel_level(symbol, "L1", "LIMIT 1")
def confirm_cancel_l1(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L1", "LIMIT 1")

def prepare_cancel_l2(symbol: str):   return _prepare_cancel_level(symbol, "L2", "LIMIT 2")
def confirm_cancel_l2(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L2", "LIMIT 2")

def prepare_cancel_l3(symbol: str):   return _prepare_cancel_level(symbol, "L3", "LIMIT 3")
def confirm_cancel_l3(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L3", "LIMIT 3")


def recompute_flags_for_symbol(symbol: str) -> None:
    """Публичный помощник для пересчёта флагов по монете."""
    _recompute_symbol_flags(symbol)


def _prepare_fill_level(symbol: str, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка пометки уровня как исполненного (FILL)."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    week = int(info.get("week") or 0)

    levels = get_pair_levels(symbol, month)
    lvl_state = levels.get(lvl) or {}
    reserved = int(lvl_state.get("reserved") or 0)

    mon_disp = month
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"

    if week <= 0:
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} FILL\n\n"
            f"Цикл ещё не запущен — пометка исполнения недоступна."
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb

    if reserved <= 0:
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} FILL\n\n"
            f"Нет открытого виртуального ордера на уровне {title} (в резерве 0 USDC)."
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_FILL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_FILL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_FILL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_FILL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_FILL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb

    msg = (
        f"{symbol} {mon_disp} Wk{week}\n"
        f"{title} FILL\n\n"
        f"Сейчас в резерве: {reserved} USDC\n"
        f"Перевести в spent: {reserved} USDC?\n\n"
        f"Пометить виртуальный {title} как полностью исполненный?"
    )
    cb = f"ORDERS_FILL_{lvl}_CONFIRM"
    kb = {
        "inline_keyboard": [[
            {"text": "CONFIRM", "callback_data": f"{cb}:{symbol}:{reserved}"},
            {"text": "↩️", "callback_data": f"ORDERS_FILL:{symbol}"},
        ]]
    }
    return msg, kb


def _confirm_fill_level(symbol: str, amount: int, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подтверждение FILL: переводим резерв в spent."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректные параметры операции.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    week = int(info.get("week") or 0)

    levels = get_pair_levels(symbol, month)
    lvl_state = levels.get(lvl) or {}
    reserved = int(lvl_state.get("reserved") or 0)
    try:
        spent = int(lvl_state.get("spent") or 0)
    except Exception:
        spent = 0
    try:
        week_quota = int(lvl_state.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1

    if reserved <= 0:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} FILL\n\n"
            f"Нечего помечать: резерв уже 0 USDC."
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_FILL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_FILL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_FILL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_FILL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_FILL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb

    try:
        requested = int(amount)
    except Exception:
        requested = 0
    if requested <= 0:
        requested = reserved
    actual = min(reserved, requested)
    new_reserved = reserved - actual
    if new_reserved < 0:
        new_reserved = 0
    new_spent = spent + actual

    # помечаем, что исполнение было в текущую неделю
    if actual > 0 and week > 0:
        last_fill_week = week

    levels[lvl] = {
        "reserved": new_reserved,
        "spent": new_spent,
        "week_quota": week_quota,
        "last_fill_week": last_fill_week,
    }
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    # После изменения резервов обновляем автофлаги (⚠️/✅/авто).
    _recompute_symbol_flags(symbol)

    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_FILL_OCO:{sym}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_FILL_L0:{sym}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_FILL_L1:{sym}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_FILL_L2:{sym}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_FILL_L3:{sym}"},
                ],
                [
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        msg = (
            f"{symbol} {mon_disp}\n"
            f"{title}: помечен как исполненный на {actual} USDC.\n"
            f"Бюджет: {info2.get('budget')} | "
            f"⏳ {info2.get('reserve')} | "
            f"💸 {info2.get('spent')} | "
            f"🎯 {info2.get('free')}"
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_FILL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_FILL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_FILL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_FILL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_FILL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb


def perform_rollover(symbol: str) -> Dict[str, Any]:
    """Роловер недели: снять виртуальные ордера, перерасчитать недельные квоты и увеличить week."""

    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    week = int(info.get("week") or 0)

    if budget <= 0 or week <= 0:
        # цикл не запущен
        return info

    # читаем уровни
    levels = get_pair_levels(symbol, month) or {}

    for lvl in LEVEL_KEYS:
        st = levels.get(lvl) or {}
        try:
            reserved = int(st.get("reserved") or 0)
        except Exception:
            reserved = 0
        try:
            spent = int(st.get("spent") or 0)
        except Exception:
            spent = 0
        try:
            week_quota = int(st.get("week_quota") or 0)
        except Exception:
            week_quota = 0
        try:
            last_fill_week = int(st.get("last_fill_week") if st.get("last_fill_week") is not None else -1)
        except Exception:
            last_fill_week = -1

        # базовая квота на следующую неделю
        base = _compute_base_quota(symbol, month, lvl, budget)

        had_fill = (last_fill_week == week)
        if had_fill:
            next_week_quota = base
        else:
            quota_prev = week_quota if week_quota > 0 else base
            next_week_quota = base + quota_prev
            if base > 0:
                max_quota = 4 * base
                if next_week_quota > max_quota:
                    next_week_quota = max_quota

        if next_week_quota < 0:
            next_week_quota = 0

        levels[lvl] = {
            "reserved": 0,  # все ордера снимаем → деньги вернутся в free
            "spent": spent,
            "week_quota": next_week_quota,
            "last_fill_week": -1,  # новая неделя — ещё не исполнялось
        }

    # сохраняем уровни и пересчитываем агрегаты
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    # ensure week increment and fresh state
    info3 = info2
    try:
        new_week = week + 1
        set_pair_week(symbol, month, new_week)
        info3 = get_pair_budget(symbol, month)
    except Exception:
        # fallback: return aggregates before week increment if anything fails
        pass
# после ролловера пересчитаем флаги
    _recompute_symbol_flags(symbol)

    return info3


# -------------------------
# OPEN ALL helpers

def _calc_available_for_level(symbol: str, month: str, week: int, lvl: str, budget: int) -> int:
    """Доступная сумма к открытию по уровню с учётом квот и already used/filled этой недели."""
    levels = get_pair_levels(symbol, month) or {}
    base_quota = _compute_base_quota(symbol, month, lvl, budget)
    if base_quota <= 0:
        return 0
    st = levels.get(lvl) or {}
    try:
        week_quota = int(st.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    quota = week_quota if week_quota > 0 else base_quota
    try:
        last_fill_week = int(st.get("last_fill_week") if st.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1
    reserved = int(st.get("reserved") or 0)
    spent_curr = int(st.get("spent") or 0) if last_fill_week == week else 0
    available = quota - (reserved + spent_curr)
    return available if available > 0 else 0


def prepare_open_all_limit(symbol: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка: открыть все лимитные уровни (🟡).
    Если свободных средств меньше общей суммы — предупреждаем и предлагаем
    открыть только ПОЛНЫЕ квоты сверху вниз (без частичных).
    """
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — ALL недоступен.", {}

    # собираем список уровней со статусом 🟡 (включая OCO) в порядке сверху-вниз
    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    yellow = {k for k,v in (flags or {}).items() if v == "🟡"}
    levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in yellow]

    # базовый план: для каждого уровня доступное «a» к открытию
    items: list[tuple[str,int]] = []
    total = 0
    for lvl in levels_list:
        a = _calc_available_for_level(symbol, month, week, lvl, budget)
        if a > 0:
            items.append((lvl, a))
            total += a

    if total <= 0:
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"}]]}
        return f"{symbol} {month}\nALL (лимит) — нечего открывать.", kb

    mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month

    if free >= total:
        # хватает на всё — обычное подтверждение
        parts = ", ".join([f"{lvl} {amt}" for lvl,amt in items])
        msg = (f"{symbol} {mon_disp} Wk{week}\n⚠️ ALL (лимит)\n\n"
               f"Открыть {len(items)} ордера на сумму {total} USDC?\nСписок: {parts}")
        kb = {"inline_keyboard":[
            [{"text":"CONFIRM","callback_data":f"ORDERS_OPEN_ALL_LIMIT_CONFIRM:{symbol}"}],
            [{"text":"MANUAL","callback_data":f"ORDERS_OPEN:{symbol}"}],
        ]}
        # сохраним план в оперативке
        try:
            _RUNTIME_PLANS[(symbol, month, "limit_all_full")] = items.copy()
        except Exception:
            pass
        return msg, kb

    # Не хватает средств — предложим открыть ПОЛНЫЕ квоты сверху вниз
    selected: list[tuple[str,int]] = []
    sel_sum = 0
    for lvl, a in items:
        if sel_sum + a <= free:
            selected.append((lvl, a))
            sel_sum += a
        else:
            continue

    if not selected:
        msg = (f"{symbol} {mon_disp} Wk{week}\n⚠️ ALL (лимит)\n\n"
               f"Доступно: {free} USDC, нужно: {total}. Недостаточно средств для любых уровней.\n"
               f"Откройте по одному или пополните баланс.")
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_OPEN:{symbol}"}]]}
        return msg, kb

    plan = ", ".join(f"{k} {q}" for k,q in items)
    will = ", ".join(f"{k} {q}" for k,q in selected)
    miss_items = [(k,q) for k,q in items if (k,q) not in selected]
    miss = ", ".join(f"{k} {q}" for k,q in miss_items) if miss_items else "—"
    msg = (f"{symbol} {mon_disp} Wk{week}\n⚠️ ALL (лимит)\n\n"
           f"Доступно: {free} USDC, нужно: {total} (не хватает {total-free}).\n"
           f"Открыть ПОЛНЫЕ квоты сверху вниз, без частичных?\n\n"
           f"План: {plan}\nБудет открыто: {will}\nПропущены: {miss}")
    kb = {"inline_keyboard":[
        [{"text":"CONFIRM","callback_data":f"ORDERS_OPEN_ALL_LIMIT_CONFIRM:{symbol}"}],
        [{"text":"MANUAL","callback_data":f"ORDERS_OPEN:{symbol}"}],
    ]}
    try:
        _RUNTIME_PLANS[(symbol, month, "limit_all_full")] = selected.copy()
    except Exception:
        pass
    return msg, kb

def confirm_open_all_limit(symbol: str) -> Tuple[str, Dict[str, Any]]:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — операция отменена.", {}

    # загрузим сохранённый план (если есть), иначе сформируем по текущим 🟡
    plan = _RUNTIME_PLANS.pop((symbol, month, "limit_all_full"), None)
    if plan is None:
        sdata = _load_symbol_data(symbol)
        flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
        yellow = {k for k,v in (flags or {}).items() if v == "🟡"}
        levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in yellow]
        plan = []
        for lvl in levels_list:
            a = _calc_available_for_level(symbol, month, week, lvl, budget)
            if a > 0:
                plan.append((lvl, a))

    levels = get_pair_levels(symbol, month) or {}
    applied: list[tuple[str,int]] = []
    total = 0

    for lvl, a in plan:
        if a <= 0:
            continue
        if free < a:
            # без частичных
            continue
        st = levels.get(lvl) or {}
        reserved = int(st.get("reserved") or 0)
        spent = int(st.get("spent") or 0)
        week_quota = int(st.get("week_quota") or 0)
        last_fill_week = int(st.get("last_fill_week") if st.get("last_fill_week") is not None else -1)
        levels[lvl] = {
            "reserved": reserved + a,
            "spent": spent,
            "week_quota": week_quota,
            "last_fill_week": last_fill_week,
        }
        free -= a
        total += a
        applied.append((lvl, a))

    save_pair_levels(symbol, month, levels)
    recompute_pair_aggregates(symbol, month)
    _recompute_symbol_flags(symbol)

    # Пересобираем карточку и остаёмся в OPEN
    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard":[
                [
                    {"text":"OCO","callback_data":f"ORDERS_OPEN_OCO:{sym}"},
                    {"text":"LIMIT 0","callback_data":f"ORDERS_OPEN_L0:{sym}"},
                    {"text":"LIMIT 1","callback_data":f"ORDERS_OPEN_L1:{sym}"},
                    {"text":"LIMIT 2","callback_data":f"ORDERS_OPEN_L2:{sym}"},
                    {"text":"LIMIT 3","callback_data":f"ORDERS_OPEN_L3:{sym}"},
                ],
                [
                    {"text":"✅ ALL","callback_data":f"ORDERS_OPEN_ALL_MKT:{sym}"},
                    {"text":"⚠️ ALL","callback_data":f"ORDERS_OPEN_ALL_LIMIT:{sym}"},
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                    {"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        # Фоллбек
        mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
        parts = ", ".join(f"{k} {q}" for k,q in applied) if applied else "—"
        return (f"{symbol} {mon_disp}\n⚠️ ALL выполнен. Открыто: {parts} на {total} USDC.",
                {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_OPEN:{symbol}"}]]})

def prepare_open_all_mkt(symbol: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка: маркет-исполнение (🟢) всех доступных уровней на их квоты."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — ALL недоступен.", {}

    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    green = {k for k,v in (flags or {}).items() if v == "🟢"}
    levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in green]

    items = []
    total = 0
    for lvl in levels_list:
        a = _calc_available_for_level(symbol, month, week, lvl, budget)
        if a > 0:
            items.append((lvl, a))
            total += a

    if total <= 0:
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"}]]}
        return f"{symbol} {month}\n✅ ALL — нечего исполнять.", kb

    mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
    parts = ", ".join([f"{lvl} {amt}" for lvl,amt in items])
    msg = (f"{symbol} {mon_disp} Wk{week}\n✅ ALL (маркет)\n\n"
           f"Исполнить {len(items)} ордеров на сумму {total} USDC?\nСписок: {parts}")
    kb = {"inline_keyboard":[
        [{"text":"CONFIRM","callback_data":f"ORDERS_OPEN_ALL_MKT_CONFIRM:{symbol}"}],
        [{"text":"CANCEL","callback_data":f"ORDERS_OPEN_ALL_MKT_CANCEL:{symbol}"}],
    ]}
    return msg, kb


def confirm_open_all_mkt(symbol: str) -> Tuple[str, Dict[str, Any]]:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — операция отменена.", {}

    levels = get_pair_levels(symbol, month) or {}
    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    green = {k for k,v in (flags or {}).items() if v == "🟢"}
    levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in green]

    applied = []
    total = 0
    for lvl in levels_list:
        a = _calc_available_for_level(symbol, month, week, lvl, budget)
        if a <= 0:
            continue
        st = levels.get(lvl) or {}
        reserved = int(st.get("reserved") or 0)
        try:
            spent = int(st.get("spent") or 0)
        except Exception:
            spent = 0
        try:
            week_quota = int(st.get("week_quota") or 0)
        except Exception:
            week_quota = 0
        # FILL: перевод в spent и фиксация недели
        levels[lvl] = {
            "reserved": reserved,
            "spent": spent + a,
            "week_quota": week_quota,
            "last_fill_week": week,
        }
        total += a
        applied.append((lvl, a))

    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)
    _recompute_symbol_flags(symbol)

    if total <= 0:
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"}]]}
        return f"{symbol} {month}\n✅ ALL — ничего не исполнено.", kb

    mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
    parts = ", ".join([f"{lvl} {amt}" for lvl,amt in applied])
    msg = (f"{symbol} {mon_disp} Wk{week}\n✅ ALL (маркет)\n\n"
           f"Исполнено {len(applied)} на сумму {total} USDC.\nСписок: {parts}")
    
    # После изменений пересобираем карточку и остаёмся в подменю OPEN
    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard":[
                [
                    {"text":"OCO","callback_data":f"ORDERS_OPEN_OCO:{sym}"},
                    {"text":"LIMIT 0","callback_data":f"ORDERS_OPEN_L0:{sym}"},
                    {"text":"LIMIT 1","callback_data":f"ORDERS_OPEN_L1:{sym}"},
                    {"text":"LIMIT 2","callback_data":f"ORDERS_OPEN_L2:{sym}"},
                    {"text":"LIMIT 3","callback_data":f"ORDERS_OPEN_L3:{sym}"},
                ],
                [
                    {"text":"✅ ALL","callback_data":f"ORDERS_OPEN_ALL_MKT:{sym}"},
                    {"text":"⚠️ ALL","callback_data":f"ORDERS_OPEN_ALL_LIMIT:{sym}"},
                    {"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        # Фоллбек: текстовое подтверждение, если сборка карточки упала
        mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
        return f"{symbol} {mon_disp}\nОперация выполнена.", kb

# -------------------------

# Публичные обёртки для FILL
def prepare_fill_oco(symbol: str):  return _prepare_fill_level(symbol, "OCO", "OCO")
def confirm_fill_oco(symbol: str, amount: int):  return _confirm_fill_level(symbol, amount, "OCO", "OCO")

def prepare_fill_l0(symbol: str):   return _prepare_fill_level(symbol, "L0", "LIMIT 0")
def confirm_fill_l0(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L0", "LIMIT 0")

def prepare_fill_l1(symbol: str):   return _prepare_fill_level(symbol, "L1", "LIMIT 1")
def confirm_fill_l1(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L1", "LIMIT 1")

def prepare_fill_l2(symbol: str):   return _prepare_fill_level(symbol, "L2", "LIMIT 2")
def confirm_fill_l2(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L2", "LIMIT 2")

def prepare_fill_l3(symbol: str):   return _prepare_fill_level(symbol, "L3", "LIMIT 3")
def confirm_fill_l3(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L3", "LIMIT 3")

def prepare_cancel_all(symbol: str):
    """Подготовка отмены всех открытых (⚠️ reserved>0) ордеров: OCO, L0–L3."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    mon_disp = month
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"
    levels = get_pair_levels(symbol, month)
    if not isinstance(levels, dict):
        levels = {}
    order_keys = ["OCO","L0","L1","L2","L3"]
    items = []
    total = 0
    for k in order_keys:
        st = levels.get(k) or {}
        r = int(st.get("reserved") or 0)
        if r > 0:
            items.append(f"{k} {r}")
            total += r
    if total <= 0:
        return (f"{symbol} {mon_disp}\n"
                f"❌ ALL — нечего отменять."), {
            "inline_keyboard":[
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{symbol}"},
                ],
                [
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{symbol}"},
                    {"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"},
                ]
            ]
        }
    msg = (f"{symbol} {mon_disp}\n"
           f"❌ ALL (cancel)\n\n"
           f"Отменить {len(items)} ордера на сумму {total} USDC?\n"
           f"Список: {', '.join(items)}")
    kb = {
        "inline_keyboard":[[
            {"text":"CONFIRM","callback_data":f"ORDERS_CANCEL_ALL_CONFIRM:{symbol}"},
            {"text":"↩️","callback_data":f"ORDERS_CANCEL:{symbol}"},
        ]]
    }
    return msg, kb


def confirm_cancel_all(symbol: str):
    """Отмена всех открытых (⚠️) ордеров — reserved→0, пересбор карточки и подменю CANCEL."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректные параметры операции.", {}
    month = datetime.now().strftime("%Y-%m")
    levels = get_pair_levels(symbol, month)
    if not isinstance(levels, dict):
        levels = {}
    changed = False
    total = 0
    for k in ["OCO","L0","L1","L2","L3"]:
        st = levels.get(k) or {}
        r = int(st.get("reserved") or 0)
        if r > 0:
            total += r
            changed = True
            levels[k] = {
                "reserved": 0,
                "spent": int(st.get("spent") or 0),
                "week_quota": int(st.get("week_quota") or 0),
                "last_fill_week": int(st.get("last_fill_week") or 0),
            }
    if not changed:
        # Нечего отменять — просто вернуть текущее подменю CANCEL
        try:
            card = build_symbol_message(symbol)
            sym = (symbol or "").upper()
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{sym}"},
                        {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{sym}"},
                        {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{sym}"},
                        {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{sym}"},
                        {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{sym}"},
                    ],
                    [
                        {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                        {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                    ],
                ]
            }
            return card, kb
        except Exception:
            return "❌ ALL — нечего отменять.", {}
    # Сохраняем и пересчитываем агрегаты/флаги
    save_pair_levels(symbol, month, levels)
    recompute_pair_aggregates(symbol, month)
    _recompute_symbol_flags(symbol)
    # Пересобираем карточку и остаёмся в CANCEL
    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{sym}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{sym}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{sym}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{sym}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{sym}"},
                ],
                [
                        {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                        {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                    ],
            ]
        }
        return card, kb
    except Exception:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        return f"{symbol} {mon_disp}\nОтменено на сумму {total} USDC.", {}