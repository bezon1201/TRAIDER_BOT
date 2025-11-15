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

BOT_VERSION = "3.9"

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

# Подключаем все роутеры, включая router режима торговли.
dp.include_router(metrics_router)
dp.include_router(dca_router)
dp.include_router(scheduler_router)
dp.include_router(data_router)
dp.include_router(trade_mode_router)


@dp.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    """Простая команда /start для ручной проверки версии бота."""
    await message.answer(f"Бот запущен. Версия {BOT_VERSION}")


@app.on_event("startup")
async def on_startup() -> None:
    """Инициализация вебхука и запуск планировщика при старте приложения."""
    logger.info("Application startup, BOT_VERSION=%s", BOT_VERSION)

    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        logger.info("Webhook set to %s", WEBHOOK_URL)
    else:
        logger.warning("WEBHOOK_BASE не задан, вебхук не будет установлен.")

    admin_chat_id_int = None
    if ADMIN_CHAT_ID:
        try:
            admin_chat_id_int = int(ADMIN_CHAT_ID)
        except ValueError:
            logger.error("ADMIN_CHAT_ID=%r не является целым числом", ADMIN_CHAT_ID)

    if admin_chat_id_int is not None:
        try:
            await bot.send_message(admin_chat_id_int, f"Бот запущен. Версия {BOT_VERSION}")
            logger.info("Стартовое сообщение админу отправлено")
        except Exception:
            logger.exception("Не удалось отправить стартовое сообщение админу")

        # Запускаем планировщик только если есть корректный admin_chat_id
        try:
            start_scheduler(bot, admin_chat_id_int, logger)
            logger.info("[scheduler] Планировщик запущен.")
        except Exception:
            logger.exception("Не удалось запустить планировщик")
    else:
        logger.warning("ADMIN_CHAT_ID не задан или некорректен, планировщик не будет отправлять сообщения админу.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Аккуратное выключение бота."""
    try:
        await bot.delete_webhook()
    except Exception:
        pass


@app.get("/health")
async def health_get() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.head("/health")
async def health_head() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> JSONResponse:
    """Основная точка входа для Telegram вебхука."""
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})
