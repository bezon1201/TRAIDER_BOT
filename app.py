# app.py
import os
import time
import hmac
import hashlib
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Response

# FastAPI application
app = FastAPI(title="Trader Bot", version="0.1.0")

def utc_now_label() -> str:
    """Return time like 'YYYY-MM-DD HH:MM UTC'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def check_binance_key() -> tuple[bool, str]:
    """
    Check if Binance API key/secret work by calling signed /api/v3/account.
    Returns (ok, detail).
    """
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        return False, "missing BINANCE_API_KEY/BINANCE_API_SECRET"

    try:
        base_url = "https://api.binance.com"
        path = "/api/v3/account"
        ts = int(time.time() * 1000)
        query = f"timestamp={ts}"
        signature = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        headers = {"X-MBX-APIKEY": api_key}
        # Requests honors HTTP(S)_PROXY from the environment automatically.
        resp = requests.get(
            base_url + path,
            params={"timestamp": ts, "signature": signature},
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return True, "ok"
        else:
            # Include short code only, don't leak body
            return False, f"status {resp.status_code}"
    except requests.RequestException as e:
        return False, "network error"

def notify_admin(text: str) -> bool:
    """Send a message to ADMIN_CHAT_ID via Telegram Bot API."""
    token = os.environ.get("BOT_TOKEN")
    chat_id = os.environ.get("ADMIN_CHAT_ID")
    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = requests.post(url, data=payload, timeout=10)
        return r.status_code == 200
    except requests.RequestException:
        return False

@app.on_event("startup")
def on_startup():
    ok, _detail = check_binance_key()
    status = "✅" if ok else "❌"
    msg = f"{utc_now_label()} Бот запущен\nBinance connection: {status}"
    notify_admin(msg)

# UptimeRobot friendly endpoints (HEAD allowed)
@app.get("/")
def root():
    return {"status": "ok"}

@app.head("/")
def root_head():
    # Explicit HEAD handler to ensure 200 on HEAD
    return Response(status_code=200)

@app.get("/health")
def health():
    return {"ok": True}

@app.head("/health")
def health_head():
    return Response(status_code=200)
