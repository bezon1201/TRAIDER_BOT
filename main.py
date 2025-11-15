import os
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from aiogram import Bot, Dispatcher, types
from aiogram.types import Update

from metrics import router as metrics_router
from dca_handlers import router as dca_router
from scheduler_handlers import router as scheduler_router
from data import router as data_router
from trade_mode_handlers import router as trade_mode_router
from scheduler import start_scheduler

BOT_VERSION = "3.2"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
WEBHOOK_BASE = os.environ.get("WEBHOOK_BASE", "").rstrip("/")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is not set. Bot will not work correctly.")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}" if BOT_TOKEN else "/webhook"
WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}" if WEBHOOK_BASE and BOT_TOKEN else None

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()

# Подключаем все роутеры
dp.include_router(metrics_router)
dp.include_router(dca_router)
dp.include_router(scheduler_router)
dp.include_router(data_router)
dp.include_router(trade_mode_router)

app = FastAPI()


@app.on_event("startup")
async def on_startup() -> None:
    """Инициализация бота, вебхука и планировщика."""
    if not bot or not BOT_TOKEN:
        logger.error("BOT_TOKEN is not configured, skipping bot startup.")
        return

    # Настройка вебхука
    try:
        if WEBHOOK_URL:
            await bot.set_webhook(WEBHOOK_URL)
            logger.info("Webhook set to %s", WEBHOOK_URL)
        else:
            await bot.delete_webhook(drop_pending_updates=True)
            logger.info("Webhook removed (no WEBHOOK_BASE set).")
    except Exception:
        logger.exception("Failed to set webhook")

    # Стартовое сообщение админу
    if ADMIN_CHAT_ID:
        try:
            admin_chat_id_int = int(ADMIN_CHAT_ID)
        except ValueError:
            logger.error("ADMIN_CHAT_ID is not a valid integer: %r", ADMIN_CHAT_ID)
        else:
            try:
                await bot.send_message(admin_chat_id_int, f"Бот запущен. Версия {BOT_VERSION}")
                logger.info("Стартовое сообщение админу отправлено")
            except Exception:
                logger.exception("Не удалось отправить стартовое сообщение админу")

            # Запуск планировщика
            try:
                start_scheduler(bot, admin_chat_id_int, logger)
                logger.info("[scheduler] Планировщик запущен.")
            except Exception:
                logger.exception("Не удалось запустить планировщик")
    else:
        logger.warning("ADMIN_CHAT_ID is not set; стартовое сообщение и планировщик не будут привязаны к админ-чату.")


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    """Основной webhook-обработчик от Telegram."""
    if not bot:
        return JSONResponse({"ok": False, "error": "BOT_TOKEN not configured"}, status_code=500)

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
    """Health-check для Render по HEAD."""
    # Тело можно не возвращать, важен статус 200
    return PlainTextResponse("")
