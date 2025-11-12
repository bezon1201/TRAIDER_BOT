import os
import logging
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
from data import DataStorage
from metrics import parse_coins_command, add_pairs
from collector import collect_all_metrics

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Env
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
WEBHOOK_URL = os.getenv('WEBHOOK_BASE', '')
PORT = int(os.getenv('PORT', 10000))
DATA_STORAGE = os.getenv('DATA_STORAGE', '/data')  # ← Render Disk path

logger.info(f"Using DATA_STORAGE: {DATA_STORAGE}")

# Init
data_storage = DataStorage(DATA_STORAGE)
app = FastAPI()
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

async def tg_send(chat_id: str, text: str) -> None:
    """Send Telegram message"""
    if not TELEGRAM_API:
        logger.warning("No TELEGRAM_API")
        return

    try:
        response = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        )
        if response.status_code == 200:
            logger.info(f"✓ Message sent to {chat_id}")
    except Exception as e:
        logger.error(f"Error sending message: {e}")

@app.on_event("startup")
async def startup():
    if ADMIN_CHAT_ID:
        await tg_send(ADMIN_CHAT_ID, "Бот запущен (v4.7 - ЧИСТАЯ СБОРКА)")

@app.get("/health")
@app.head("/health")
async def health():
    return {"ok": True}

@app.get("/")
@app.head("/")
async def root():
    return {"ok": True, "service": "traider-bot", "version": "4.7"}

@app.post("/telegram")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except:
        data = {}

    message = data.get("message", {})
    text = (message.get("text") or "").strip()
    chat_id = str((message.get("chat") or {}).get("id") or "")

    if not chat_id or not text:
        return JSONResponse({"ok": True})

    logger.info(f"Message from {chat_id}: {text[:50]}")

    # /start
    if text.lower() == "/start":
        await tg_send(chat_id, "✓ Бот готов!\n/coins PAIR1 PAIR2 - добавить пары\n/now - собрать метрики\n/data - список файлов")
        return JSONResponse({"ok": True})

    # /coins
    if text.lower().startswith('/coins'):
        pairs_list = parse_coins_command(text)
        if not pairs_list:
            await tg_send(chat_id, "❌ Укажите пары: /coins BTCUSDT ETHUSDT")
            return JSONResponse({"ok": True})

        success, all_pairs = add_pairs(DATA_STORAGE, pairs_list)
        if success:
            await tg_send(chat_id, f"✓ Пары обновлены ({len(all_pairs)}):\n" + ", ".join(all_pairs))
        else:
            await tg_send(chat_id, "❌ Ошибка")
        return JSONResponse({"ok": True})

    # /now - ГЛАВНАЯ КОМАНДА СБОРА МЕТРИК
    if text.lower() == "/now":
        logger.info(f"Collecting metrics from {chat_id}...")
        try:
            results = await collect_all_metrics(DATA_STORAGE, delay_ms=50)
            success = sum(1 for v in results.values() if v)
            total = len(results)
            logger.info(f"✓ Collection: {success}/{total}")
        except Exception as e:
            logger.error(f"Collection error: {e}")

        return JSONResponse({"ok": True})

    # /data
    if text.lower() == "/data":
        files = data_storage.get_files_list()
        msg = f"📁 Файлов: {len(files)}\n" + "\n".join(files) if files else "Пусто"
        await tg_send(chat_id, msg)
        return JSONResponse({"ok": True})

    # Default
    await tg_send(chat_id, "❓ Неизвестная команда")
    return JSONResponse({"ok": True})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
