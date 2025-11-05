#!/usr/bin/env python3
import os
import time
import hmac
import hashlib
from datetime import datetime
from typing import Optional, Dict, Any

import httpx
from fastapi import FastAPI, Request, Response

APP_START_TS = datetime.utcnow()

app = FastAPI(title="Traider Bot", version="0.1.2")


async def _binance_check_credentials() -> bool:
    """
    Verify Binance API key/secret by calling signed endpoint /api/v3/account.
    Returns True if HTTP 200, otherwise False.
    """
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        return False

    ts = int(time.time() * 1000)
    query = f"timestamp={ts}&recvWindow=5000"
    signature = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
    url = f"https://api.binance.com/api/v3/account?{query}&signature={signature}"
    headers = {"X-MBX-APIKEY": api_key}

    timeout = httpx.Timeout(20.0)

    # Rely on trust_env=True so HTTP(S)_PROXY and NO_PROXY are honored by httpx/httpcore
    async with httpx.AsyncClient(timeout=timeout, trust_env=True) as client:
        try:
            r = await client.get(url, headers=headers)
            return r.status_code == 200
        except Exception:
            return False


async def _tg_send_admin(text: str) -> None:
    """
    Send a text message to ADMIN_CHAT_ID via Telegram Bot API.
    """
    token = os.environ.get("BOT_TOKEN")
    chat_id = os.environ.get("ADMIN_CHAT_ID")
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    timeout = httpx.Timeout(20.0)
    # trust_env=True will honor proxies from env
    async with httpx.AsyncClient(timeout=timeout, trust_env=True) as client:
        try:
            await client.post(url, json=payload)
        except Exception:
            pass


@app.on_event("startup")
async def _on_startup() -> None:
    # Check Binance connectivity/credentials
    ok = await _binance_check_credentials()

    # Compose startup message
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = f"{ts} Бот запущен\nBinance connection: {'✅' if ok else '❌'}"
    await _tg_send_admin(msg)


# Explicitly allow GET and HEAD for health checks (avoids 405 on HEAD)
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root() -> Dict[str, Any]:
    return {"ok": True, "uptime_sec": (datetime.utcnow() - APP_START_TS).total_seconds()}


@app.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
async def health() -> Dict[str, Any]:
    return {"status": "ok"}


# Telegram webhooks
@app.post("/telegram")
async def telegram_webhook(req: Request) -> Dict[str, Any]:
    """
    Minimal webhook handler (legacy path). Echoes OK.
    """
    try:
        payload = await req.json()
    except Exception:
        payload = None
    print("Webhook /telegram hit", {"has_json": payload is not None})
    return {"ok": True}


@app.post("/webhook")
async def telegram_webhook_plain(req: Request) -> Dict[str, Any]:
    """
    Fallback webhook without token in path (some setups use querystring).
    """
    try:
        payload = await req.json()
    except Exception:
        payload = None
    print("Webhook /webhook hit", {"has_json": payload is not None})
    return {"ok": True}


@app.post("/webhook/{token:path}")
async def telegram_webhook_token(token: str, req: Request) -> Dict[str, Any]:
    """
    Webhook path that includes the Bot token (arbitrary characters).
    We don't validate here yet; just return ok.
    """
    try:
        payload = await req.json()
    except Exception:
        payload = None
    print("Webhook /webhook/{token} hit", {"token_len": len(token), "has_json": payload is not None})
    return {"ok": True}
