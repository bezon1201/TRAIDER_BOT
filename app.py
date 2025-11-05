#!/usr/bin/env python3
import os
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

import httpx
from fastapi import FastAPI, Request
from portfolio import build_portfolio_message

APP_START_TS = datetime.utcnow()
app = FastAPI(title="Traider Bot", version="0.2.0")


async def _binance_check_credentials() -> bool:
    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        return False
    import time, hmac, hashlib
    ts = int(time.time() * 1000)
    query = f"timestamp={ts}&recvWindow=5000"
    signature = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"https://api.binance.com/api/v3/account?{query}&signature={signature}"
    headers = {"X-MBX-APIKEY": api_key}
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=True) as client:
        try:
            r = await client.get(url, headers=headers)
            return r.status_code == 200
        except Exception:
            return False


async def _tg_send_admin(text: str) -> None:
    token = os.environ.get("BOT_TOKEN")
    chat_id = os.environ.get("ADMIN_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=True) as client:
        try:
            await client.post(url, json=payload)
        except Exception:
            pass


async def _tg_reply(chat_id: int, text: str) -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=True) as client:
        try:
            await client.post(url, json=payload)
        except Exception:
            pass


@app.on_event("startup")
async def _on_startup() -> None:
    ok = await _binance_check_credentials()
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    msg = f"{ts} Бот запущен\nBinance connection: {'✅' if ok else '❌'}"
    await _tg_send_admin(msg)


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root() -> Dict[str, Any]:
    return {"ok": True, "uptime_sec": (datetime.utcnow() - APP_START_TS).total_seconds()}


@app.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
async def health() -> Dict[str, Any]:
    return {"status": "ok"}


def _extract_text(update: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    for key in ("message", "channel_post"):
        if key in update and isinstance(update[key], dict):
            chat = update[key].get("chat") or {}
            chat_id = chat.get("id")
            txt = update[key].get("text") or update[key].get("caption")
            return (chat_id, txt)
    return (None, None)


def _is_portfolio_trigger(text: str) -> bool:
    if not text:
        return False
    t = text.casefold().strip()
    if "💼" in t:
        return True
    if t.startswith("/"):
        cmd = t.split()[0].split("@")[0]
        return cmd == "/portfolio"
    return t == "portfolio"


@app.post("/telegram")
async def telegram_webhook(req: Request) -> Dict[str, Any]:
    try:
        update = await req.json()
    except Exception:
        return {"ok": True}
    chat_id, text = _extract_text(update)
    if chat_id and text and _is_portfolio_trigger(text):
        msg = await build_portfolio_message()
        await _tg_reply(chat_id, msg)
    return {"ok": True}


@app.post("/webhook")
async def telegram_webhook_plain(req: Request) -> Dict[str, Any]:
    try:
        update = await req.json()
    except Exception:
        return {"ok": True}
    chat_id, text = _extract_text(update)
    if chat_id and text and _is_portfolio_trigger(text):
        msg = await build_portfolio_message()
        await _tg_reply(chat_id, msg)
    return {"ok": True}


@app.post("/webhook/{token:path}")
async def telegram_webhook_token(token: str, req: Request) -> Dict[str, Any]:
    try:
        update = await req.json()
    except Exception:
        return {"ok": True}
    chat_id, text = _extract_text(update)
    if chat_id and text and _is_portfolio_trigger(text):
        msg = await build_portfolio_message()
        await _tg_reply(chat_id, msg)
    return {"ok": True}
