import logging
from pathlib import Path

from telegram.ext import Application
from config import BOT_TOKEN, ADMIN_CHAT_ID, APP_VERSION
from handlers import register_handlers

# ---------- ЛОГИРОВАНИЕ ----------

LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)

# файл всегда перезаписываем (mode="w") → свежий лог на каждый запуск
file_handler = logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8", mode="w")
file_handler.setLevel(logging.INFO)

# в консоль выводим только наше важное (без спама httpx)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[file_handler, console_handler],
)

log = logging.getLogger(__name__)

# глушим болтовню httpx и telegram.request до WARNING
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.request").setLevel(logging.WARNING)


# ---------- ХУК ПОСЛЕ ЗАПУСКА ПРИЛОЖЕНИЯ ----------

async def on_startup(app: Application) -> None:
    """Отправляем сообщение админу при запуске бота."""
    if not ADMIN_CHAT_ID:
        log.warning("ADMIN_CHAT_ID не задан, пропускаю сообщение о запуске.")
        return

    msg = f"Бот запущен. Версия {APP_VERSION}"
    try:
        await app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)
        log.info("Отправлено сообщение админу (%s): %s", ADMIN_CHAT_ID, msg)
    except Exception as e:
        log.exception("Не удалось отправить сообщение админу: %s", e)


# ---------- ТОЧКА ВХОДА ----------

def main() -> None:
    if not BOT_TOKEN:
        log.error("BOT_TOKEN не задан. Проверь .env")
        raise SystemExit("BOT_TOKEN не задан")

    log.info("Запуск приложения Telegram. Версия %s", APP_VERSION)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)  # вызовется один раз при старте
        .build()
    )

    # Регистрируем хэндлеры в отдельном модуле
    register_handlers(app)

    log.info("Запускаю long polling (drop_pending_updates=True)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
