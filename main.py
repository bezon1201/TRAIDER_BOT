import os
import logging
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
from data import DataStorage
from metrics import parse_coins_command, add_pairs, remove_pairs, read_pairs
from collector import collect_all_metrics
from market_calculation import force_market_mode

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
WEBHOOK_URL = os.getenv('WEBHOOK_BASE', '')
PORT = int(os.getenv('PORT', 10000))
DATA_STORAGE = os.getenv('DATA_STORAGE', '/data')

logger.info(f"Using DATA_STORAGE: {DATA_STORAGE}")

data_storage = DataStorage(DATA_STORAGE)
app = FastAPI()

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

async def tg_send(chat_id: str, text: str) -> None:
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

async def tg_send_file(chat_id: str, file_path: str, filename: str) -> bool:
    if not TELEGRAM_API:
        return False
    try:
        with open(file_path, 'rb') as f:
            files_data = {"document": (filename, f, "application/octet-stream")}
            response = await client.post(
                f"{TELEGRAM_API}/sendDocument",
                data={"chat_id": chat_id},
                files=files_data
            )
            if response.status_code == 200:
                logger.info(f"✓ File sent: {filename}")
                return True
            else:
                logger.error(f"File send error: {response.status_code}")
                return False
    except Exception as e:
        logger.error(f"Error sending file: {e}")
        return False

@app.on_event("startup")
async def startup():
    if ADMIN_CHAT_ID:
        await tg_send(ADMIN_CHAT_ID, "✅ Бот запущен (v5.5)")

@app.get("/health")
@app.head("/health")
async def health():
    return {"ok": True}

@app.get("/")
@app.head("/")
async def root():
    return {"ok": True, "service": "traider-bot", "version": "5.5"}

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

    if text.lower() == "/start":
        help_msg = ("✅ Бот готов (v5.5)!\n\n"
                   "📝 Команды:\n"
                   "/coins - показать список пар\n"
                   "/coins PAIR1 PAIR2 - добавить пары\n"
                   "/coins delete PAIR1 PAIR2 - удалить пары\n"
                   "/now - собрать метрики\n"
                   "/market force 12+6 - market_mode для 12+6\n"
                   "/market force 4+2 - market_mode для 4+2\n"
                   "/data show all - список файлов\n"\n                   "/data export all - отправить все\n"
                   "/data delete all - удалить все\n"
                   "/data delete file1.xxx, file2.xxx - удалить конкретные")
        await tg_send(chat_id, help_msg)
        return JSONResponse({"ok": True})

    if text.lower().startswith('/coins'):
        action, pairs_list = parse_coins_command(text)

        if action == 'list':
            all_pairs = read_pairs(DATA_STORAGE)
            if all_pairs:
                msg = f"📊 Активные пары ({len(all_pairs)}):\n" + ", ".join(all_pairs)
            else:
                msg = "📊 Список пар пуст"
            await tg_send(chat_id, msg)

        elif action == 'delete':
            if not pairs_list:
                await tg_send(chat_id, "❌ Укажите пары для удаления")
                return JSONResponse({"ok": True})
            success, remaining = remove_pairs(DATA_STORAGE, pairs_list)
            if success:
                await tg_send(chat_id, f"✓ Пары удалены ({len(remaining)} осталось)")
            else:
                await tg_send(chat_id, "❌ Ошибка")

        else:
            if not pairs_list:
                await tg_send(chat_id, "❌ Укажите пары")
                return JSONResponse({"ok": True})
            success, all_pairs = add_pairs(DATA_STORAGE, pairs_list)
            if success:
                await tg_send(chat_id, f"✓ Пары обновлены ({len(all_pairs)})\n" + ", ".join(all_pairs))
            else:
                await tg_send(chat_id, "❌ Ошибка")

        return JSONResponse({"ok": True})

    if text.lower() == "/now":
        logger.info(f"Collecting metrics...")
        try:
            results = await collect_all_metrics(DATA_STORAGE, delay_ms=50)
            success = sum(1 for v in results.values() if v)
            total = len(results)
            await tg_send(chat_id, f"✓ Метрики: {success}/{total}")
        except Exception as e:
            logger.error(f"Collection error: {e}")
            await tg_send(chat_id, "❌ Ошибка")
        return JSONResponse({"ok": True})

    if text.lower().startswith('/market force'):
        parts = text.split()
        if len(parts) < 3:
            await tg_send(chat_id, "❌ Используйте: /market force 12+6 или /market force 4+2")
            return JSONResponse({"ok": True})

        frame = parts[2]
        if frame not in ["12+6", "4+2"]:
            await tg_send(chat_id, "❌ Фрейм должен быть 12+6 или 4+2")
            return JSONResponse({"ok": True})

        all_pairs = read_pairs(DATA_STORAGE)
        if not all_pairs:
            await tg_send(chat_id, "❌ Нет пар в списке")
            return JSONResponse({"ok": True})

        results = []
        for symbol in all_pairs:
            result = force_market_mode(DATA_STORAGE, symbol, frame)
            results.append(f"{symbol}: {result}")

        msg = f"market_mode для фрейма {frame}:\n" + "\n".join(results)
        await tg_send(chat_id, msg)
        return JSONResponse({"ok": True})

    # v5.5 ИСПРАВЛЕНИЕ: добавить список файлов через запятую
    if text.lower() == "/data":
        files = data_storage.get_files_list()
        if files:
            msg = f"📁 Файлов: {len(files)}\n" + ", ".join(files)
        else:
            msg = "📁 Хранилище пусто"
        await tg_send(chat_id, msg)
        return JSONResponse({"ok": True})

    if text.lower() == "/data delete all":
        files = data_storage.get_files_list()
        if not files:
            await tg_send(chat_id, "📁 Уже пусто")
        else:
            if data_storage.delete_all():
                await tg_send(chat_id, f"✓ Удалено {len(files)} файл(ов)")
            else:
                await tg_send(chat_id, "❌ Ошибка")
        return JSONResponse({"ok": True})

    if text.lower().startswith("/data delete ") and text.lower() != "/data delete all":
        args = text[13:].strip()
        if not args:
            await tg_send(chat_id, "❌ Укажите файлы: /data delete file1.xxx, file2.xxx")
            return JSONResponse({"ok": True})

        filenames = [f.strip() for f in args.split(",") if f.strip()]
        deleted = []
        failed = []

        for filename in filenames:
            if data_storage.delete_file(filename):
                deleted.append(filename)
            else:
                failed.append(filename)

        msg = f"✓ Удалено: {len(deleted)}"
        if deleted:
            msg += f"\n  {', '.join(deleted)}"
        if failed:
            msg += f"\n❌ Не найдены: {len(failed)}"

        await tg_send(chat_id, msg)
        return JSONResponse({"ok": True})

    if text.lower() == "/data export all":
        files = data_storage.get_files_list()
        if not files:
            await tg_send(chat_id, "📁 Нечего экспортировать")
        else:
            await tg_send(chat_id, f"📤 Отправляю {len(files)} файл(ов)")
            success_count = 0
            for filename in files:
                file_path = data_storage.get_file_path(filename)
                if file_path:
                    try:
                        result = await tg_send_file(chat_id, str(file_path), filename)
                        if result:
                            success_count += 1
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        logger.error(f"Error: {e}")
            await tg_send(chat_id, f"✓ Отправлено: {success_count}")
        return JSONResponse({"ok": True})

    await tg_send(chat_id, "❓ Неизвестная команда")
    return JSONResponse({"ok": True})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
