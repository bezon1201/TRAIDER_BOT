
import os
from datetime import datetime, timezone
from fastapi import FastAPI, Request
import httpx

from portfolio import build_portfolio_message

BOT_TOKEN = os.getenv("TRAIDER_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("TRAIDER_ADMIN_CAHT_ID", "").strip()
WEBHOOK_BASE = os.getenv("TRAIDER_WEBHOOK_BASE") or os.getenv("WEBHOOK_BASE") or ""
METRIC_CHAT_ID = os.getenv("TRAIDER_METRIC_CHAT_ID", "").strip()
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
app = FastAPI()
client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

async def tg_send(chat_id: str, text: str) -> None:
    if not TELEGRAM_API:
        return
    try:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})
    except Exception:
        pass

async def _binance_ping() -> str:
    url = "https://api.binance.com/api/v3/ping"
    try:
        r = await client.get(url)
        return "✅" if r.status_code == 200 else f"❌ {r.status_code}"
    except Exception as e:
        return f"❌ {e.__class__.__name__}: {e}"

@app.on_event("startup")
async def on_startup():
    ping = await _binance_ping()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = f"{now_utc} Бот запущен\nBinance connection: {ping}"
    if ADMIN_CHAT_ID:
        await tg_send(ADMIN_CHAT_ID, msg)

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/telegram")
async def telegram_webhook(update: Request):
    try:
        data = await update.json()
    except Exception:
        data = {}
    message = data.get("message") or data.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    chat_id = str((message.get("chat") or {}).get("id") or "")
    if not chat_id:
        return {"ok": True}

    if text.startswith("/portfolio"):
        try:
            reply = await build_portfolio_message(client, BINANCE_API_KEY, BINANCE_API_SECRET)
        except Exception as e:
            reply = f"Ошибка портфеля: {e}"
        await tg_send(chat_id, reply or "Нет данных.")
        return {"ok": True}

    return {"ok": True}
