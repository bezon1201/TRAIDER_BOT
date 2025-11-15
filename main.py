import os
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Update

from trade_mode import router as trade_router
from metrics import router as metrics_router
from dca_handlers import router as dca_router
from scheduler_handlers import router as scheduler_router
from data import router as data_router
from scheduler import start_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ENV переменные ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"])
WEBHOOK_BASE = os.environ["WEBHOOK_BASE"].rstrip("/")

# путь вебхука (уникальный на основе токена)
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}"

# --- Telegram / Aiogram ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.include_router(trade_router)
dp.include_router(metrics_router)
dp.include_router(dca_router)
dp.include_router(scheduler_router)
dp.include_router(data_router)

# --- FastAPI-приложение ---
app = FastAPI(title="Trader Bot 3.3")


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """Простейший хэндлер /start, чтобы проверить, что бот жив.""" 
    await message.answer("Бот онлайн. Версия 3.3")


@app.on_event("startup")
async def on_startup():
    """Настройка вебхука и уведомление админа при старте сервиса.""" 
    try:
        await bot.set_webhook(WEBHOOK_URL)
        logger.info("Webhook set to %s", WEBHOOK_URL)
    except Exception:
        logger.exception("Не удалось установить webhook")

    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text="Бот запущен. Версия 3.3",
        )
        logger.info("Стартовое сообщение админу отправлено")
    except Exception:
        logger.exception("Не удалось отправить сообщение админу")

    # Запускаем фоновый планировщик
    try:
        start_scheduler(bot, ADMIN_CHAT_ID, logger)
    except Exception:
        logger.exception("Не удалось запустить планировщик")


@app.on_event("shutdown")
async def on_shutdown():
    """Чистое выключение: удаляем webhook и закрываем сессию бота.""" 
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        logger.exception("Не удалось удалить webhook")

    await bot.session.close()


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    """Точка входа для Telegram webhook.""" 
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    # Telegram ожидает любой 200-ответ
    return JSONResponse({"ok": True})


@app.get("/health")
async def health_get():
    """Health-check для Render по GET.""" 
    return PlainTextResponse("ok")


@app.head("/health")
async def health_head():
    """Health-check для Render по HEAD (как в ТЗ).""" 
    # Тело можно не возвращать, важен статус 200
    return PlainTextResponse("")