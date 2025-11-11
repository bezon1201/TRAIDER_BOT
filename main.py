import os
import logging
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
from pathlib import Path
from data import DataStorage
from metrics import parse_coins_command, add_pairs
from collector import collect_all_metrics

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
WEBHOOK_URL = os.getenv('WEBHOOK_BASE', '')
PORT = int(os.getenv('PORT', 10000))
DATA_STORAGE = os.getenv('DATA_STORAGE', '/tmp/storage')

# Initialize data storage
data_storage = DataStorage(DATA_STORAGE)

# FastAPI app
app = FastAPI()

# Telegram API
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

# HTTP client
client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

async def tg_send(chat_id: str, text: str) -> None:
    """Send message to Telegram"""
    if not TELEGRAM_API:
        logger.warning("TELEGRAM_API not configured")
        return

    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        response = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json=payload,
        )

        if response.status_code != 200:
            logger.error(f"Telegram API error: {response.status_code}")
        else:
            logger.info(f"Message sent successfully to {chat_id}")

    except Exception as e:
        logger.error(f"Error sending message: {e}")

@app.on_event("startup")
async def startup_event():
    """Send startup message and set webhook"""
    if ADMIN_CHAT_ID:
        await tg_send(ADMIN_CHAT_ID, "Бот запущен (FastAPI v4.3 с расчетом SMA14/ATR14)")

    # Set webhook
    if WEBHOOK_URL and BOT_TOKEN:
        try:
            webhook_path = f"{WEBHOOK_URL}/telegram"
            payload = {"url": webhook_path}

            response = await client.post(
                f"{TELEGRAM_API}/setWebhook",
                json=payload,
            )

            if response.status_code == 200:
                logger.info(f"Webhook set to: {webhook_path}")
            else:
                logger.error(f"Failed to set webhook: {response.status_code}")

        except Exception as e:
            logger.error(f"Error setting webhook: {e}")

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"ok": True}

@app.head("/health")
async def health_head():
    """Health check endpoint (HEAD)"""
    return {"ok": True}

@app.get("/")
async def root():
    """Root endpoint"""
    return {"ok": True, "service": "traider-bot", "version": "4.3"}

@app.head("/")
async def root_head():
    """Root endpoint (HEAD)"""
    return {"ok": True}

@app.post("/telegram")
async def telegram_webhook(request: Request):
    """Handle Telegram updates"""
    try:
        data = await request.json()
    except Exception:
        data = {}

    message = data.get("message", {})
    text = (message.get("text") or "").strip()
    chat_id = str((message.get("chat") or {}).get("id") or "")

    if not chat_id or not text:
        return JSONResponse({"ok": True})

    logger.info(f"Received message from {chat_id}: {text[:100]}")

    # Handle /start command
    if text.lower() == "/start":
        await tg_send(chat_id, "Привет! Бот запущен (FastAPI v4.3 с расчетом индикаторов).")
        return JSONResponse({"ok": True})

    # Handle /coins command
    if text.lower().startswith('/coins'):
        pairs_list = parse_coins_command(text)

        if not pairs_list:
            await tg_send(chat_id, "Ошибка: укажите пары.\nПример: /coins BTCUSDT ETHUSDT")
            return JSONResponse({"ok": True})

        # Добавляем пары в файл
        success, all_pairs = add_pairs(DATA_STORAGE, pairs_list)

        if success:
            pairs_str = ', '.join(all_pairs)
            response_msg = ('✅ Пары обновлены.\n\n' +
                          'Всего пар: ' + str(len(all_pairs)) + '\n' +
                          'Список:\n' + pairs_str)
            await tg_send(chat_id, response_msg)
        else:
            await tg_send(chat_id, "❌ Ошибка при обновлении пар")

        return JSONResponse({"ok": True})

    # Handle /now command - collect metrics silently
    if text.lower() == "/now":
        logger.info(f"Starting metrics collection (12h, 6h, 4h, 2h with SMA14/ATR14) from {chat_id}")

        try:
            results = await collect_all_metrics(DATA_STORAGE, delay_ms=50)
            success_count = sum(1 for v in results.values() if v)
            total_count = len(results)

            if total_count > 0:
                logger.info(f"Metrics collected: {success_count}/{total_count}")
        except Exception as e:
            logger.error(f"Error during metrics collection: {e}")

        # Не отправляем сообщение в Telegram (тихо)
        return JSONResponse({"ok": True})

    # Handle /data command
    if text.lower() == "/data":
        files = data_storage.get_files_list()
        if files:
            files_str = ', '.join(files)
            response_msg = 'Файлы в хранилище:\n' + files_str
        else:
            response_msg = 'Хранилище пусто'

        await tg_send(chat_id, response_msg)
        return JSONResponse({"ok": True})

    # Handle /data delete all
    if text.lower() == "/data delete all":
        files = data_storage.get_files_list()
        if not files:
            await tg_send(chat_id, "Хранилище уже пусто")
        else:
            if data_storage.delete_all():
                count_str = str(len(files))
                response_msg = '✅ Удалено ' + count_str + ' файл(ов)'
                await tg_send(chat_id, response_msg)
            else:
                await tg_send(chat_id, "❌ Ошибка при удалении файлов")
        return JSONResponse({"ok": True})

    # Handle /data export all
    if text.lower() == "/data export all":
        files = data_storage.get_files_list()
        if not files:
            await tg_send(chat_id, "Нечего экспортировать - хранилище пусто")
        else:
            count_str = str(len(files))
            await tg_send(chat_id, 'Отправляю ' + count_str + ' файл(ов)...')

            for filename in files:
                file_path = data_storage.get_file_path(filename)
                if file_path:
                    try:
                        with open(file_path, 'rb') as f:
                            files_to_send = {"document": (filename, f, "application/json")}
                            form_data = {"chat_id": chat_id}

                            response = await client.post(
                                f"{TELEGRAM_API}/sendDocument",
                                data=form_data,
                                files=files_to_send,
                            )

                            if response.status_code == 200:
                                logger.info(f"Exported: {filename}")
                    except Exception as e:
                        logger.error(f"Error exporting {filename}: {e}")
                        error_msg = 'Ошибка при отправке ' + filename
                        await tg_send(chat_id, error_msg)

            count_str = str(len(files))
            success_msg = '✅ Экспортировано ' + count_str + ' файл(ов)'
            await tg_send(chat_id, success_msg)
        return JSONResponse({"ok": True})

    # Default response
    help_text = ('Неизвестная команда.\nДоступные команды:\n' +
                '/start - приветствие\n' +
                '/coins - добавить пары для сбора метрик\n' +
                '/now - собрать метрики (12h, 6h, 4h, 2h с SMA14/ATR14)\n' +
                '/data - список файлов\n' +
                '/data export all - отправить все файлы\n' +
                '/data delete all - удалить все файлы')
    await tg_send(chat_id, help_text)

    return JSONResponse({"ok": True})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
