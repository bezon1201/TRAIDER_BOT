import os
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Update

from metrics import router as metrics_router
from data import router as data_router
from coin_state import router as coin_state_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"])
WEBHOOK_BASE = os.environ["WEBHOOK_BASE"].rstrip("/")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.include_router(metrics_router)
dp.include_router(data_router)
dp.include_router(coin_state_router)

app = FastAPI(title="Trader Bot 1.8")


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("Бот онлайн. Версия 1.8")


@app.on_event("startup")
async def on_startup():
    try:
        await bot.set_webhook(WEBHOOK_URL)
        logger.info("Webhook set to %s", WEBHOOK_URL)
    except Exception:
        logger.exception("Не удалось установить webhook")

    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text="Бот запущен. Версия 1.8",
        )
        logger.info("Стартовое сообщение админу отправлено")
    except Exception:
        logger.exception("Не удалось отправить сообщение админу")


@app.on_event("shutdown")
async def on_shutdown():
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        logger.exception("Не удалось удалить webhook")
    await bot.session.close()


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})


@app.get("/health")
async def health_get():
    return PlainTextResponse("ok")


@app.head("/health")
async def health_head():
    return PlainTextResponse("")
