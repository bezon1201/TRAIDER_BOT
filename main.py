import os
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Update

from metrics import router as metrics_router
from dca_handlers import router as dca_router
from scheduler_handlers import router as scheduler_router
from data import router as data_router
from scheduler import start_scheduler
from trade_mode_handlers import handle_trade_and_pin

BOT_VERSION = "3.7"

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
WEBHOOK_BASE = os.environ.get("WEBHOOK_BASE", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}" if WEBHOOK_BASE else None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

app = FastAPI()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Подключаем все роутеры.
dp.include_router(metrics_router)
dp.include_router(dca_router)
dp.include_router(scheduler_router)
dp.include_router(data_router)

# Регистрируем обработчик /trade mode и PIN напрямую на Dispatcher.
dp.message.register(handle_trade_and_pin)


@dp.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    await message.answer(f"Бот запущен. Версия {BOT_VERSION}")


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Application startup, BOT_VERSION=%s", BOT_VERSION)

    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        logger.info("Webhook set to %s", WEBHOOK_URL)
    else:
        logger.warning("WEBHOOK_BASE is not set, webhook URL is empty.")

    admin_chat_id_int = None
    if ADMIN_CHAT_ID:
        try:
            admin_chat_id_int = int(ADMIN_CHAT_ID)
        except ValueError:
            logger.warning("ADMIN_CHAT_ID=%r is not a valid integer", ADMIN_CHAT_ID)

    if admin_chat_id_int is not None:
        try:
            await bot.send_message(admin_chat_id_int, f"Бот запущен. Версия {BOT_VERSION}")
            logger.info("Стартовое сообщение админу отправлено")
        except Exception:
            logger.exception("Не удалось отправить стартовое сообщение админу")

        try:
            start_scheduler(bot, admin_chat_id_int, logger)
            logger.info("[scheduler] Планировщик запущен.")
        except Exception:
            logger.exception("Не удалось запустить планировщик")
    else:
        logger.warning("ADMIN_CHAT_ID не задан, планировщик не будет отправлять сообщения админу.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("Application shutdown")
    await bot.session.close()


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> JSONResponse:
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
