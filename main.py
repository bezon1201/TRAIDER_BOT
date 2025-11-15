import os
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from aiogram import Bot, Dispatcher
from aiogram.types import Update

from metrics import router as metrics_router
from dca_handlers import router as dca_router
from scheduler_handlers import router as scheduler_router
from data import router as data_router
from trade_mode_handlers import router as trade_mode_router
from scheduler import start_scheduler

BOT_VERSION = "3.4"

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
WEBHOOK_BASE = os.environ.get("WEBHOOK_BASE", "").rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

bot = Bot(token=BOT_TOKEN)
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
    """Инициализация вебхука, отправка стартового сообщения и запуск планировщика."""
    if WEBHOOK_BASE:
        webhook_url = f"{WEBHOOK_BASE}{WEBHOOK_PATH}"
        await bot.set_webhook(webhook_url)
        logger.info("Webhook set to %s", webhook_url)
    else:
        logger.warning("WEBHOOK_BASE is not set. Webhook is not configured.")

    # Стартовое сообщение админу с номером версии
    if ADMIN_CHAT_ID:
        try:
            admin_chat_id_int = int(ADMIN_CHAT_ID)
            await bot.send_message(admin_chat_id_int, f"Бот запущен. Версия {BOT_VERSION}")
            logger.info("Стартовое сообщение админу отправлено")
        except Exception:
            logger.exception("Не удалось отправить стартовое сообщение админу")
            admin_chat_id_int = None
    else:
        admin_chat_id_int = None

    # Запускаем планировщик
    if admin_chat_id_int is None:
        admin_chat_id_for_scheduler = 0
    else:
        admin_chat_id_for_scheduler = admin_chat_id_int

    start_scheduler(bot, admin_chat_id_for_scheduler, logger)
    logger.info("[scheduler] Планировщик запущен.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Аккуратно закрываем HTTP-сессию бота при остановке приложения."""
    await bot.session.close()
    logger.info("Bot session closed")


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    """Приём апдейтов от Telegram через вебхук."""
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
