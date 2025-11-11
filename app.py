import logging
import os
from typing import Optional

from fastapi import FastAPI, Request, Response
import uvicorn

from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from utils import mono, html_escape
import data_module

def env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if (v is not None and v != "") else default

BOT_TOKEN = env("TRAIDER_BOT_TOKEN") or env("TRADER_BOT_TOKEN")
ADMIN_CHAT_ID = env("TRAIDER_ADMIN_CAHT_ID") or env("TRAIDER_ADMIN_CHAT_ID")
WEBHOOK_BASE = env("WEBHOOK_BASE", "")
HTTP_PROXY = env("HTTP_PROXY")
HTTPS_PROXY = env("HTTPS_PROXY")

ADMIN_KEY = env("ADMIN_KEY")
BINANCE_API_KEY = env("BINANCE_API_KEY")
BINANCE_API_SECRET = env("BINANCE_API_SECRET")
COLLECT_INTERVAL_SEC = env("COLLECT_INTERVAL_SEC")
RAW_MAX_BYTES = env("RAW_MAX_BYTES")
STORAGE_DIR = env("STORAGE_DIR")

if not BOT_TOKEN:
    raise RuntimeError("TRAIDER_BOT_TOKEN is required")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

proxy = HTTPS_PROXY or HTTP_PROXY
try:
    session = AiohttpSession(proxy=proxy) if proxy else AiohttpSession()
except Exception as e:
    logging.warning("Proxy session failed (%s). Falling back to direct session.", e)
    session = AiohttpSession()

bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
dp.include_router(data_module.router)
import merics_module
dp.include_router(merics_module.router)

app = FastAPI()

@app.head("/")
async def head_root():
    return Response(status_code=200)

@app.get("/")
async def get_root():
    return {"ok": True, "service": "trader-bot", "webhook": True}

@app.post("/tg")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = types.Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.head("/tg")
async def head_tg():
    return Response(status_code=200)


async def on_startup():
    # ensure storage exists for /data module
    try:
        data_module.ensure_storage_dir(STORAGE_DIR)
    except Exception:
        pass

    if WEBHOOK_BASE:
        url = WEBHOOK_BASE.rstrip("/") + "/tg"
        await bot.set_webhook(url)
        logging.info("Webhook set to %s", url)
    else:
        logging.info("WEBHOOK_BASE is empty — webhook not set")

    if ADMIN_CHAT_ID:
        try:
            await bot.send_message(int(ADMIN_CHAT_ID), mono("Бот запущен"))
            logging.info("Startup message sent to admin chat %s", ADMIN_CHAT_ID)
        except Exception as e:
            logging.exception("Failed to send admin startup message: %s", e)
    else:
        logging.warning("TRAIDER_ADMIN_CAHT_ID/TRAIDER_ADMIN_CHAT_ID not set — cannot notify admin")

@app.on_event("startup")
async def _app_startup():
    await on_startup()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
