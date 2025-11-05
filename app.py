# app.py
import os
import time
import hmac
import hashlib
import json
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from portfolio import build_portfolio_message

APP_URL = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
PROXY_URL = os.getenv("PROXY_URL")
STICKER_PORTFOLIO_ID = os.getenv("STICKER_PORTFOLIO_ID")

TIMEOUT = httpx.Timeout(15.0, connect=15.0)

# httpx 0.27.x uses "proxies" kwarg
client = httpx.AsyncClient(timeout=TIMEOUT, proxies=PROXY_URL if PROXY_URL else None)

app = FastAPI()

@app.on_event("startup")
async def on_startup():
    # Ping Binance (signed /account) to check keys
    binance_ok = False
    detail = ""
    try:
        ts = int(time.time() * 1000)
        q = f"timestamp={ts}&recvWindow=60000"
        sig = hmac.new(
            (BINANCE_API_SECRET or "").encode(),
            q.encode(),
            hashlib.sha256
        ).hexdigest()
        url = f"https://api.binance.com/api/v3/account?{q}&signature={sig}"
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY or ""}
        r = await client.get(url, headers=headers)
        binance_ok = r.status_code == 200
        detail = "✅" if binance_ok else f"❌ {r.status_code} {r.text[:120]}"
    except Exception as e:
        detail = f"❌ {e.__class__.__name__}: {e}"

    # Notify admin about start
    if BOT_TOKEN and ADMIN_CHAT_ID:
        now_utc = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        text = f"{now_utc} Бот запущен\nBinance connection: {detail}"
        await send_telegram_message(ADMIN_CHAT_ID, text, parse_mode="HTML")

    # Ensure webhook if APP_URL present
    if APP_URL and BOT_TOKEN:
        try:
            wh_url = APP_URL.rstrip("/") + f"/webhook/{BOT_TOKEN}"
            await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                             params={"url": wh_url, "drop_pending_updates": True})
        except Exception:
            pass


async def send_telegram_message(chat_id: str, text: str, parse_mode: Optional[str] = None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
        data["disable_web_page_preview"] = True
    await client.post(url, data=data)

@app.get("/", response_class=PlainTextResponse)
@app.head("/", response_class=PlainTextResponse)
async def root():
    return "ok"

@app.post("/webhook/{BOT_TOKEN}")
async def tg_webhook(request: Request):
    upd = await request.json()
    msg = upd.get("message") or upd.get("edited_message")
    cq = upd.get("callback_query")
    chat_id = None
    text = None
    sticker_id = None

    if msg:
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id"))
        text = (msg.get("text") or "").strip()
        sticker = msg.get("sticker")
        if sticker:
            sticker_id = sticker.get("file_unique_id")
    elif cq:
        chat = cq.get("message", {}).get("chat", {})
        chat_id = str(chat.get("id"))
        text = (cq.get("data") or "").strip()

    # Trigger /portfolio by command, by emoji 💼, or by configured sticker id
    need_portfolio = False
    if text and text.startswith("/portfolio"):
        need_portfolio = True
    if not need_portfolio and text and "💼" in text:
        need_portfolio = True
    if not need_portfolio and STICKER_PORTFOLIO_ID and sticker_id == STICKER_PORTFOLIO_ID:
        need_portfolio = True

    if need_portfolio and chat_id:
        try:
            msg_text = await build_portfolio_message(client, BINANCE_API_KEY, BINANCE_API_SECRET, PROXY_URL)
            # Send as monospace block via HTML <pre>
            await send_telegram_message(chat_id, f"<pre>{html_escape(msg_text)}</pre>", parse_mode="HTML")
        except Exception as e:
            await send_telegram_message(chat_id, f"Ошибка формирования портфеля: {e.__class__.__name__}: {e}")

    return Response(status_code=200)

def html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
