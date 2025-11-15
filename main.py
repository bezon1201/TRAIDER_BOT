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
from trade_mode_handlers import router as trade_mode_router
from scheduler import start_scheduler

BOT_VERSION = "3.4"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
WEBHOOK_BASE = os.environ.get("WEBHOOK_BASE")
if not WEBHOOK_BASE:
    raise RuntimeError("WEBHOOK_BASE is not set")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}"

app = FastAPI()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Подключаем все router'ы
dp.include_router(metrics_router)
dp.include_router(dca_router)
dp.include_router(scheduler_router)
dp.include_router(data_router)
dp.include_router(trade_mode_router)


@dp.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    """Простейший /start с отображением версии."""
    await message.answer(f"Бот запущен. Версия {BOT_VERSION}")


@app.on_event("startup")
async def on_startup() -> None:
    """Настройка вебхука, стартовое сообщение и запуск планировщика."""
    await bot.set_webhook(WEBHOOK_URL)
    logger.info("Webhook set to %s", WEBHOOK_URL)

    admin_chat_id_int = None
    if ADMIN_CHAT_ID:
        try:
            admin_chat_id_int = int(ADMIN_CHAT_ID)
        except ValueError:
            logger.error("ADMIN_CHAT_ID имеет некорректное значение: %r", ADMIN_CHAT_ID)

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


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> JSONResponse:
    """Основной webhook-эндпойнт Telegram."""
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    # Telegram ожидает любой 200-ответ
    return JSONResponse({"ok": True})


@app.get("/health")
async def health_get() -> PlainTextResponse:
    """Health-check для Render по GET."""
    return PlainTextResponse("ok")


@app.head("/health")
async def health_head() -> PlainTextResponse:
    """Health-check для Render по HEAD (как в ТЗ)."""
    # Тело можно не возвращать, важен статус 200
    return PlainTextResponse("")
