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

app = FastAPI(title="Traider Bot", version="0.1.0")


def _get_proxies() -> Optional[dict]:
    """
    Build httpx proxies dict from env. Respects both upper and lower case.
    Returns None if no proxies configured.
    """
    def g(k: str) -> Optional[str]:
        v = os.environ.get(k) or os.environ.get(k.lower())
        return v.strip() if v else None

    http_proxy = g("HTTP_PROXY")
    https_proxy = g("HTTPS_PROXY")
    proxies = {}
    if http_proxy:
        proxies["http://"] = http_proxy
    if https_proxy:
        proxies["https://"] = https_proxy

    return proxies or None


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

    proxies = _get_proxies()
    timeout = httpx.Timeout(20.0)

    async with httpx.AsyncClient(proxies=proxies, timeout=timeout, trust_env=True) as client:
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

    proxies = _get_proxies()
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(proxies=proxies, timeout=timeout, trust_env=True) as client:
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


@app.get("/", include_in_schema=False)
async def root() -> Dict[str, Any]:
    return {"ok": True, "uptime_sec": (datetime.utcnow() - APP_START_TS).total_seconds()}


@app.head("/", include_in_schema=False)
async def root_head() -> Response:
    return Response(status_code=200)


@app.get("/health", include_in_schema=False)
async def health() -> Dict[str, Any]:
    return {"status": "ok"}


@app.head("/health", include_in_schema=False)
async def health_head() -> Response:
    return Response(status_code=200)


@app.post("/telegram")
async def telegram_webhook(req: Request) -> Dict[str, Any]:
    """
    Minimal webhook handler. Echoes OK for now.
    """
    try:
        _ = await req.json()
    except Exception:
        pass
    return {"ok": True}
