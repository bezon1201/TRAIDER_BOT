import os
from dotenv import load_dotenv

# Загружаем .env (локально), на Render это не помешает
load_dotenv()

# Текущая версия бота — будем обновлять при новых релизах
APP_VERSION = "1.53"

# Основные настройки из .env
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)

ADMIN_KEY = os.getenv("ADMIN_KEY")

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

HTTP_PROXY = os.getenv("HTTP_PROXY")
HTTPS_PROXY = os.getenv("HTTPS_PROXY")

TF1 = os.getenv("TF1", "12h")
TF2 = os.getenv("TF2", "6h")

try:
    MARKET_PUBLISH = int(os.getenv("MARKET_PUBLISH", "24"))
except ValueError:
    MARKET_PUBLISH = 24

# Папка для рабочих файлов бота (trade_mode.json и т.п.)
STORAGE_DIR = os.getenv("STORAGE_DIR", "./data")
os.makedirs(STORAGE_DIR, exist_ok=True)
