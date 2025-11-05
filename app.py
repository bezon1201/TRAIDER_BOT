
import os
import time
import hmac
import hashlib
from typing import Any, Dict

import httpx
from fastapi import FastAPI, Request

from portfolio import build_portfolio_card

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

BINANCE_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()

TIMEOUT = httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=None)

app = FastAPI()
client: httpx.AsyncClient | None = None

@app.on_event("startup")
async def _startup():
    global client
    # trust_env=True lets httpx pick HTTP(S)_PROXY from env (Tinyproxy)
    client = httpx.AsyncClient(timeout=TIMEOUT, trust_env=True)

@app.on_event("shutdown")
async def _shutdown():
    if client:
        await client.aclose()

@app.get("/")
async def root():
    return {"ok": True, "service": "TRAIDER_BOT"}

async def _send_message(chat_id: int | str, text: str):
    assert client is not None
    payload = {
        "chat_id": chat_id,
        "text": f"```\n{text}\n```",
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
    await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)

@app.post("/tg")
async def telegram_webhook(update: Request):
    data: Dict[str, Any] = await update.json()
    msg = data.get("message") or data.get("edited_message") or {}
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()

    if not text:
        return {"ok": True}

    if text.startswith("/start"):
        await _send_message(chat_id, "Бот запущен. Отправь /portfolio")
        return {"ok": True}

    if text.startswith("/portfolio"):
        assert client is not None
        card = await build_portfolio_card(client, BINANCE_KEY, BINANCE_SECRET)
        await _send_message(chat_id, card)
        return {"ok": True}

    # echo unknown
    await _send_message(chat_id, "Не знаю такую команду. Попробуй /portfolio")
    return {"ok": True}
