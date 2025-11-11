\
import os
import logging
from fastapi import FastAPI, Request, Response
from aiogram import Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import Bot, DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from logging_setup import setup_logging
from utils import getenv_str, getenv_int

# external routers (kept stubs here)
import data_module
import metrics_module
import market_module
import scheduler_module

setup_logging()
logger = logging.getLogger("app")

# --- Env
TRAIDER_BOT_TOKEN = getenv_str("TRAIDER_BOT_TOKEN")
ADMIN_CHAT_ID = getenv_int("ADMIN_CHAT_ID", 0)
STORAGE_DIR = getenv_str("STORAGE_DIR", "./storage")
PROXY_URL = getenv_str("PROXY_URL")

os.makedirs(STORAGE_DIR, exist_ok=True)

# --- Aiogram bot & dispatcher
session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else AiohttpSession()
bot = Bot(token=TRAIDER_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML), session=session)
dp = Dispatcher()

# Routers connect (placeholders)
dp.include_router(data_module.router)
dp.include_router(metrics_module.router)
dp.include_router(market_module.router)
dp.include_router(scheduler_module.router)

# --- FastAPI app
app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True}

@app.head("/")
async def head_root():
    # for uptime checks
    return Response(status_code=200)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    # Send admin a monospaced "Bot started"
    if ADMIN_CHAT_ID:
        text = "```\nБот запущен\n```"
        try:
            await bot.send_message(ADMIN_CHAT_ID, text)
        except Exception as e:
            logger.error("failed to notify admin: %s", e)
    else:
        logger.warning("ADMIN_CHAT_ID not set; skip admin notify")
