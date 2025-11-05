import os
import hmac, hashlib
from datetime import datetime, timezone
import json
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response

from portfolio import build_portfolio_message

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID") or os.getenv("ADMIN_CHAT_ID".upper())
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE", "").rstrip("/")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

HTTP_PROXY = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
HTTPS_PROXY = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
PROXY_URL = HTTPS_PROXY or HTTP_PROXY

TIMEOUT = httpx.Timeout(20.0, connect=20.0)

# Use transport= to be compatible with httpx>=0.27 and 0.28+
transport = httpx.AsyncHTTPTransport(proxy=PROXY_URL) if PROXY_URL else httpx.AsyncHTTPTransport()
client = httpx.AsyncClient(timeout=TIMEOUT, transport=transport)

tg_base = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI()

# ------------- helpers -------------
async def tg(method: str, **params):
    if not tg_base:
        return {"ok": False, "description": "BOT_TOKEN is empty"}
    resp = await client.post(f"{tg_base}/{method}", data=params)
    try:
        return resp.json()
    except Exception:
        return {"ok": False, "description": f"HTTP {resp.status_code}"}

def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def _sign_binance(query: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()

async def check_binance_account() -> bool:
    try:
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            return False
        ms = int(datetime.now(timezone.utc).timestamp()*1000)
        q = f"timestamp={ms}&recvWindow=60000"
        sig = _sign_binance(q, BINANCE_API_SECRET)
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        r = await client.get(
            "https://api.binance.com/api/v3/account",
            params=dict(timestamp=ms, recvWindow=60000, signature=sig),
            headers=headers,
        )
        return r.status_code == 200
    except Exception:
        return False

def _expected_webhook() -> Optional[str]:
    if not WEBHOOK_BASE:
        return None
    return WEBHOOK_BASE + "/tg"

async def ensure_webhook() -> tuple[bool, str]:
    if not BOT_TOKEN:
        return False, "BOT_TOKEN not set"
    want = _expected_webhook()
    if not want:
        return False, "WEBHOOK_BASE not set"
    info = await tg("getWebhookInfo")
    cur = (info.get("result") or {}).get("url") if isinstance(info, dict) else None
    if cur != want:
        await tg("setWebhook", url=want, allowed_updates=json.dumps(["message","callback_query","sticker"]))
        info = await tg("getWebhookInfo")
        cur = (info.get("result") or {}).get("url")
    return (cur == want), cur or ""

async def notify_admin(text: str, parse_mode: Optional[str]="HTML"):
    if not ADMIN_CHAT_ID:
        return
    await tg("sendMessage", chat_id=ADMIN_CHAT_ID, text=text, parse_mode=parse_mode)

# ------------- startup -------------
@app.on_event("startup")
async def on_startup():
    wh_ok, wh_detail = await ensure_webhook()
    ok = await check_binance_account()
    status = "✅" if ok else "❌"
    await notify_admin(f"{utc_now_str()} Бот запущен\nBinance connection: {status}\nWebhook: {'✅' if wh_ok else '❌'}", parse_mode=None)

# ------------- health -------------
@app.get("/", response_class=Response)
@app.head("/", response_class=Response)
@app.get("/health", response_class=Response)
@app.head("/health", response_class=Response)
async def health():
    return Response(content="ok", media_type="text/plain")

# ------------- telegram webhook -------------
def _parse_stickers_env() -> set[str]:
    raw = os.getenv("PORTFOLIO_STICKERS","").strip()
    if not raw:
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}

STICKER_IDS = _parse_stickers_env()
_last_sticker_ts = 0

@app.post("/tg")
async def tg_webhook(req: Request):
    global _last_sticker_ts
    upd = await req.json()
    msg = upd.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    sticker = msg.get("sticker")

    if not chat_id:
        return {"ok": True}

    # Sticker trigger for portfolio
    if sticker:
        fid = sticker.get("file_unique_id")
        now = int(datetime.now(timezone.utc).timestamp())
        if fid and fid in STICKER_IDS and now - _last_sticker_ts >= 5:
            _last_sticker_ts = now
            try:
                payload = await build_portfolio_message(client, BINANCE_API_KEY, BINANCE_API_SECRET, STORAGE_DIR)
            except Exception:
                payload = "Ошибка формирования портфеля."
            await tg("sendMessage", chat_id=chat_id, text=payload, parse_mode="HTML")
            return {"ok": True}
        if fid and fid not in STICKER_IDS and ADMIN_CHAT_ID:
            await notify_admin(f"Неизвестный стикер: {fid}", parse_mode=None)

    if text.lower() == "/start":
        await tg("sendMessage", chat_id=chat_id, text="✅ Я работаю. Команды: /start, /portfolio")
        return {"ok": True}

    if text.lower().startswith("/portfolio"):
        try:
            payload = await build_portfolio_message(client, BINANCE_API_KEY, BINANCE_API_SECRET, STORAGE_DIR)
        except Exception:
            payload = "Ошибка формирования портфеля."
        await tg("sendMessage", chat_id=chat_id, text=payload, parse_mode="HTML")
        return {"ok": True}

    # default echo for debug
    await tg("sendMessage", chat_id=chat_id, text="Команда не распознана. Попробуй /portfolio")
    return {"ok": True}
