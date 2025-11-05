# app.py
import os
import time
import hmac
import hashlib
import json
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Response, Request

# FastAPI application
app = FastAPI(title="Trader Bot", version="0.2.1")

# ------------- helpers -------------
def utc_now_label() -> str:
    """Return time like 'YYYY-MM-DD HH:MM UTC'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)

def tg_api(method: str, **params) -> requests.Response | None:
    """Call Telegram Bot API. Returns response or None on network error."""
    token = env("BOT_TOKEN")
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        return requests.post(url, data=params, timeout=10)
    except requests.RequestException:
        return None

def notify_admin(text: str) -> bool:
    """Send a message to ADMIN_CHAT_ID via Telegram Bot API."""
    token = env("BOT_TOKEN")
    chat_id = env("ADMIN_CHAT_ID")
    if not token or not chat_id:
        return False
    r = tg_api("sendMessage", chat_id=chat_id, text=text)
    return bool(r and r.status_code == 200)

def check_binance_key() -> tuple[bool, str]:
    """
    Check if Binance API key/secret work by calling signed /api/v3/account.
    Returns (ok, detail).
    """
    api_key = env("BINANCE_API_KEY")
    api_secret = env("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        return False, "missing BINANCE_API_KEY/BINANCE_API_SECRET"

    try:
        base_url = "https://api.binance.com"
        path = "/api/v3/account"
        ts = int(time.time() * 1000)
        query = f"timestamp={ts}"
        signature = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        headers = {"X-MBX-APIKEY": api_key}
        resp = requests.get(
            base_url + path,
            params={"timestamp": ts, "signature": signature},
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return True, "ok"
        else:
            return False, f"status {resp.status_code}"
    except requests.RequestException:
        return False, "network error"

def ensure_webhook():
    """
    Ensure Telegram webhook equals WEBHOOK_BASE + '/tg'.
    Uses env WEBHOOK_BASE and BOT_TOKEN. No secret is stored in code.
    """
    base = env("WEBHOOK_BASE").rstrip("/")
    token = env("BOT_TOKEN")
    if not base or not token:
        return False, "missing WEBHOOK_BASE/BOT_TOKEN"

    desired = f"{base}/tg"
    r_info = tg_api("getWebhookInfo")
    current = None
    if r_info and r_info.status_code == 200:
        try:
            current = r_info.json().get("result", {}).get("url")
        except Exception:
            current = None
    if current == desired:
        return True, "ok"

    r_set = tg_api(
        "setWebhook",
        url=desired,
        drop_pending_updates=True,
        allowed_updates=json.dumps(["message", "callback_query"]),
        max_connections=40,
    )
    if r_set and r_set.status_code == 200 and r_set.json().get("ok"):
        return True, "set"
    else:
        return False, "setWebhook failed"

# ------------- lifecycle -------------
@app.on_event("startup")
def on_startup():
    # Ping Binance
    ok, _detail = check_binance_key()
    status = "✅" if ok else "❌"

    # Ensure webhook
    wh_ok, wh_detail = ensure_webhook()
    wh_status = "✅" if wh_ok else "❌"

    # Notify admin
    msg = (
        f"{utc_now_label()} Бот запущен\n"
        f"Binance connection: {status}\n"
        f"Webhook: {wh_status}"
    )
    notify_admin(msg)

# ------------- uptime endpoints (HEAD allowed) -------------
@app.get("/")
def root():
    return {"status": "ok"}

@app.head("/")
def root_head():
    return Response(status_code=200)

@app.get("/health")
def health():
    return {"ok": True}

@app.head("/health")
def health_head():
    return Response(status_code=200)

# ------------- Telegram webhook receiver -------------
@app.post("/tg")
async def tg_webhook(request: Request):
    """
    Telegram will POST updates here, because setWebhook uses WEBHOOK_BASE + '/tg'.
    """
    data = await request.json()
    # Basic message handling
    message = data.get("message") or data.get("edited_message")
    if message:
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
        if text.startswith("/start"):
            tg_api("sendMessage", chat_id=chat_id, text="👋 Привет! Бот на связи.")
        else:
            tg_api("sendMessage", chat_id=chat_id, text="✅ Я работаю. Напишите /start.")
    return {"ok": True}
