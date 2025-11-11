
import os, asyncio, logging
import logging_setup
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or "0")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
STORAGE_DIR = os.getenv("STORAGE_DIR", "./storage")
PROXY = os.getenv("PROXY_URL", "")

app = FastAPI()
logger = logging_setup.setup_logging()

@app.head("/webhook")
async def head_webhook():
    logging.getLogger("app").info("component=http action=ping method=HEAD path=/webhook status=200")
    return {}

@app.get("/")
async def root():
    return {"ok": True}

session = AiohttpSession(proxy=PROXY) if PROXY else AiohttpSession()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML), session=session)
dp = Dispatcher()

import data_module
import metrics_module
import market_module
import scheduler_module

dp.include_router(data_module.router)
dp.include_router(metrics_module.router)
dp.include_router(market_module.router)
dp.include_router(scheduler_module.router)

@app.post("/webhook")
async def webhook(request: Request):
    update = types.Update.model_validate(await request.json())
    await dp.feed_update(bot, update)
    return {"ok": True}

_scheduler_stop_event = asyncio.Event()
_scheduler_task = None

@app.on_event("startup")
async def _startup():
    logger.info("component=http action=app_start")
    os.makedirs(STORAGE_DIR, exist_ok=True)
    try:
        if ADMIN_CHAT_ID:
            text = "```\nБот запущен\n```"
            await bot.send_message(ADMIN_CHAT_ID, text, parse_mode=None)
    except Exception:
        logger.warning("component=bot action=notify_admin_fail", exc_info=True)
    global _scheduler_task
    _scheduler_task = asyncio.create_task(scheduler_module.run_scheduler_loop(_scheduler_stop_event))

@app.on_event("shutdown")
async def _shutdown():
    logger.info("component=http action=app_shutdown")
    _scheduler_stop_event.set()
    try:
        await _scheduler_task
    except Exception:
        pass
    await bot.session.close()
