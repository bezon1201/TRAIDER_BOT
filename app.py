
import asyncio
import logging
import os
from typing import Optional

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
import uvicorn

# Aiogram 3.x
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

# ---------- Env & Config ----------

def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if (v is not None and v != "") else default

BOT_TOKEN = env("TRAIDER_BOT_TOKEN") or env("TRADER_BOT_TOKEN")  # fallback just in case
ADMIN_CHAT_ID = env("TRAIDER_ADMIN_CAHT_ID") or env("TRAIDER_ADMIN_CHAT_ID")
WEBHOOK_BASE = env("WEBHOOK_BASE", "")
HTTP_PROXY = env("HTTP_PROXY")
HTTPS_PROXY = env("HTTPS_PROXY")

# optional, here for parity with old bot's envs
ADMIN_KEY = env("ADMIN_KEY")
BINANCE_API_KEY = env("BINANCE_API_KEY")
BINANCE_API_SECRET = env("BINANCE_API_SECRET")
COLLECT_INTERVAL_SEC = env("COLLECT_INTERVAL_SEC")
RAW_MAX_BYTES = env("RAW_MAX_BYTES")
STORAGE_DIR = env("STORAGE_DIR")

if not BOT_TOKEN:
    raise RuntimeError("TRAIDER_BOT_TOKEN is required")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ---------- Bot session with optional proxy ----------

proxy = HTTPS_PROXY or HTTP_PROXY
session = AiohttpSession(proxy=proxy) if proxy else AiohttpSession()

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML, session=session)
dp = Dispatcher()

# ---------- FastAPI app ----------

app = FastAPI()

@app.head("/")
async def head_root():
    # for uptime checkers (e.g., render/fly/healthchecks)
    return Response(status_code=200)

@app.get("/")
async def get_root():
    return {"ok": True, "service": "trader-bot", "webhook": True}

# Telegram webhook endpoint
@app.post("/tg")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = types.Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.head("/tg")
async def head_tg():
    # allow HEAD on webhook for uptime probes as requested
    return Response(status_code=200)

# ---------- Basic handlers ----------

@dp.message()
async def echo_fallback(msg: types.Message):
    # Minimal placeholder: just acknowledge any text message
    # You will extend logic later.
    await msg.answer("Принято ✅")

# ---------- Startup tasks ----------

async def on_startup():
    # Set webhook if WEBHOOK_BASE provided
    if WEBHOOK_BASE:
        url = WEBHOOK_BASE.rstrip("/") + "/tg"
        await bot.set_webhook(url)
        logging.info("Webhook set to %s", url)
    else:
        logging.info("WEBHOOK_BASE is empty — webhook not set")

    # Notify admin
    if ADMIN_CHAT_ID:
        try:
            await bot.send_message(int(ADMIN_CHAT_ID), "Бот запущен")
            logging.info("Startup message sent to admin chat %s", ADMIN_CHAT_ID)
        except Exception as e:
            logging.exception("Failed to send admin startup message: %s", e)
    else:
        logging.warning("TRAIDER_ADMIN_CAHT_ID/TRAIDER_ADMIN_CHAT_ID not set — cannot notify admin")

# Run startup after app starts (uvicorn)
@app.on_event("startup")
async def _app_startup():
    await on_startup()

# ---------- Entrypoint ----------

if __name__ == "__main__":
    # Bind 0.0.0.0 for containers/PaaS; port from $PORT if present
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
