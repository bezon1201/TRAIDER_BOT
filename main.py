import os
import logging
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
from data import DataStorage
from metrics import parse_coins_command, add_pairs, remove_pairs, read_pairs, set_mode
from collector import collect_all_metrics
from market_calculation import force_market_mode
from metric_scheduler import MetricScheduler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
WEBHOOK_URL = os.getenv('WEBHOOK_BASE', '')
PORT = int(os.getenv('PORT', 10000))
DATA_STORAGE = os.getenv('DATA_STORAGE', '/data')

logger.info(f"Using DATA_STORAGE: {DATA_STORAGE}")

data_storage = DataStorage(DATA_STORAGE)
scheduler: MetricScheduler = None
scheduler_task: asyncio.Task = None

app = FastAPI()

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)


async def tg_send(chat_id: str, text: str, markdown: bool = True) -> None:
    if not TELEGRAM_API:
        logger.warning("No TELEGRAM_API")
        return
    try:
        payload = {"chat_id": chat_id, "text": text}
        # Markdown отключен глобально, чтобы не ловить 400 от Telegram
        response = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json=payload
        )
        if response.status_code == 200:
            logger.info(f"✓ Message sent to {chat_id}")
        else:
            logger.error(f"Telegram sendMessage error {response.status_code}: {response.text}")
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
    global scheduler, scheduler_task

    scheduler = MetricScheduler(DATA_STORAGE)
    scheduler_task = asyncio.create_task(scheduler.start_loop())

    if ADMIN_CHAT_ID:
        await tg_send(ADMIN_CHAT_ID, "✅ Бот запущен (v6.0)\n⏲️ Планировщик активен")


@app.on_event("shutdown")
async def shutdown():
    global scheduler, scheduler_task
    if scheduler:
        scheduler.stop_loop()
    if scheduler_task:
        try:
            await asyncio.wait_for(scheduler_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Scheduler task did not stop in time")


@app.get("/health")
@app.head("/health")
async def health():
    return {"ok": True}


@app.get("/")
@app.head("/")
async def root():
    return {"ok": True, "service": "traider-bot", "version": "6.0"}


@app.post("/telegram")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except:
        data = {}

    message = data.get("message", {})
    # ВАЖНО: используем и text, и caption (для документов)
    text = (message.get("text") or message.get("caption") or "").strip()
    chat_id = str((message.get("chat") or {}).get("id") or "")

    if not chat_id or not text:
        return JSONResponse({"ok": True})

    logger.info(f"Message from {chat_id}: {text[:50]}")

    # Нормализация команды (поддержка /cmd@BotName ...)
    lower_text = text.lower()
    parts = lower_text.split(maxsplit=1)
    cmd_token = parts[0] if parts else ""
    cmd_root = cmd_token.split("@", 1)[0]
    tail_lower = parts[1].strip() if len(parts) > 1 else ""

    if cmd_root == "/start" and tail_lower == "":
        help_msg = (
            "✅ Бот готов (v6.0)!\n\n"
            "📝 Команды:\n"
            "/coins - показать список пар\n"
            "/coins PAIR1 PAIR2 - добавить пары\n"
            "/coins delete PAIR1 PAIR2 - удалить пары\n"
            "/now - собрать метрики\n"
            "/market force 12+6 - market_mode для 12+6\n"
            "/market force 4+2 - market_mode для 4+2\n"
            "/data - список файлов\n"
            "/data import - импортировать присланный файл (caption)\n"
            "/data export all - отправить все\n"
            "/data delete all - удалить все\n"
            "/data delete file1.xxx, file2.xxx - удалить конкретные\n"
            "/scheduler config - показать конфиг\n"
            "/scheduler period <P> - период [900…86400] сек\n"
            "/scheduler publish <N> - публикация [1…96] часов\n"
            "/scheduler on | off - включить/отключить"
        )
        await tg_send(chat_id, help_msg)
        return JSONResponse({"ok": True})

    # === КОМАНДЫ ПЛАНИРОВЩИКА ===

    if cmd_root == "/scheduler" and tail_lower in ("config", "confyg"):
        config = scheduler.get_config()
        msg = (
            f"⚙️ Конфиг планировщика:\n"
            f"period: {config['period']}s\n"
            f"publish: {config['publish_interval_hours']}h\n"
            f"enabled: {'✅' if config['enabled'] else '❌'}\n"
            f"last_published: {config['last_published'][:10]}"
        )
        await tg_send(chat_id, msg, markdown=False)
        return JSONResponse({"ok": True})

    if cmd_root == "/scheduler" and tail_lower.startswith("period "):
        try:
            new_period = int(text.split()[-1])
            if scheduler.update_period(new_period):
                await tg_send(chat_id, f"✅ Период: {new_period} сек")
            else:
                await tg_send(chat_id, f"❌ Диапазон: 900…86400 сек")
        except:
            await tg_send(chat_id, "❌ Некорректное значение")
        return JSONResponse({"ok": True})

    if cmd_root == "/scheduler" and tail_lower.startswith("publish "):
        try:
            new_interval = int(text.split()[-1])
            if scheduler.update_publish_interval(new_interval):
                await tg_send(chat_id, f"✅ Публикация: {new_interval} часов")
            else:
                await tg_send(chat_id, f"❌ Диапазон: 1…96 часов")
        except:
            await tg_send(chat_id, "❌ Некорректное значение")
        return JSONResponse({"ok": True})

    if cmd_root == "/scheduler" and tail_lower == "on":
        scheduler.toggle_scheduler(True)
        await tg_send(chat_id, "✅ Планировщик включен")
        return JSONResponse({"ok": True})

    if cmd_root == "/scheduler" and tail_lower == "off":
        scheduler.toggle_scheduler(False)
        await tg_send(chat_id, "✅ Планировщик отключен")
        return JSONResponse({"ok": True})

    # === КОМАНДЫ МОНЕТ ===

    
    # New in 1.3: /coins long|short SYMBOL...
    if lower_text.startswith('/coins long') or lower_text.startswith('/coins short'):
        parts = text.strip().split()
        mode = 'LONG' if len(parts) > 1 and parts[1].lower() == 'long' else 'SHORT'
        symbols = [p.strip().upper() for p in parts[2:] if p.strip()]
        if not symbols:
            await tg_send(chat_id, "❌ Укажите пары: /coins long BTCUSDT ETHUSDT или /coins short ...")
            return JSONResponse({"ok": True})
        results = []
        for s in symbols:
            res = set_mode(DATA_STORAGE, s, mode)
            results.append(f"{s}: {res}")
        await tg_send(chat_id, "✅ Mode обновлён:\n" + "\n".join(results))
        return JSONResponse({"ok": True})
    if lower_text.startswith('/coins'):
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
                if remaining:
                    msg = f"✓ Пары обновлены ({len(remaining)}):\n" + ", ".join(remaining)
                else:
                    msg = "✓ Все пары удалены, список пуст"
                await tg_send(chat_id, msg)
            else:
                await tg_send(chat_id, "❌ Ошибка")
        else:
            if not pairs_list:
                await tg_send(chat_id, "❌ Укажите пары")
                return JSONResponse({"ok": True})
            success, all_pairs = add_pairs(DATA_STORAGE, pairs_list)
            if success:
                await tg_send(chat_id, f"✓ Пары обновлены ({len(all_pairs)}):\n" + ", ".join(all_pairs))
            else:
                await tg_send(chat_id, "❌ Ошибка")

        return JSONResponse({"ok": True})

    # === СБОР МЕТРИК ===

    if lower_text == "/now":
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

    # === MARKET_MODE ===

    if cmd_root == "/market" and tail_lower.startswith("force"):
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

    # === РАБОТА С ФАЙЛАМИ ===

    # импорт файла(ов): отправь документ с Caption "/data import" (или "/data@Bot import")
    if cmd_root == "/data" and tail_lower.startswith("import"):
        # один document (стандарт), поддержим и список documents на всякий
        docs = []
        doc = message.get("document")
        if doc:
            docs.append(doc)
        more_docs = message.get("documents") or []
        if isinstance(more_docs, list):
            docs.extend([d for d in more_docs if isinstance(d, dict)])

        if not docs:
            await tg_send(chat_id, "❌ Пришлите файл как *документ* с подписью `/data import`")
            return JSONResponse({"ok": True})

        saved, failed = [], []

        for d in docs:
            file_id = d.get("file_id")
            filename = d.get("file_name") or f"file_{file_id}.bin"
            filename = os.path.basename(filename) or "file.bin"  # страховка от путей

            try:
                # 1) получить путь файла у Telegram
                resp = await client.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
                data_json = resp.json()
                if resp.status_code != 200 or not data_json.get("ok"):
                    raise RuntimeError(f"getFile failed: {data_json}")

                file_path = data_json["result"]["file_path"]

                # 2) скачать содержимое
                file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                file_resp = await client.get(file_url)
                if file_resp.status_code != 200:
                    raise RuntimeError(f"download http {file_resp.status_code}")

                content = file_resp.content

                # 3) сохранить (перезапись, если уже есть)
                if data_storage.save_file(filename, content):
                    saved.append(f"{filename} ({len(content)} B)")
                else:
                    failed.append(filename)

                await asyncio.sleep(0)
            except Exception as e:
                logger.error(f"Import error for {filename}: {e}")
                failed.append(filename)

        msg = "📥 Импорт завершён"
        if saved:
            msg += f"\n✓ Сохранены: {', '.join(saved)}"
        if failed:
            msg += f"\n❌ Ошибки: {', '.join(failed)}"
        await tg_send(chat_id, msg)
        return JSONResponse({"ok": True})

    if cmd_root == "/data" and tail_lower == "":
        files = data_storage.get_files_list()
        if files:
            msg = f"📁 Файлов: {len(files)}\n" + ", ".join(files)
        else:
            msg = "📁 Хранилище пусто"
        await tg_send(chat_id, msg, markdown=False)
        return JSONResponse({"ok": True})

    if cmd_root == "/data" and tail_lower == "delete all":
        files = data_storage.get_files_list()
        if not files:
            await tg_send(chat_id, "📁 Уже пусто")
        else:
            if data_storage.delete_all():
                await tg_send(chat_id, f"✓ Удалено {len(files)} файл(ов)")
            else:
                await tg_send(chat_id, "❌ Ошибка")
        return JSONResponse({"ok": True})

    if cmd_root == "/data" and tail_lower.startswith("delete ") and tail_lower != "delete all":
        args = text[13:].strip()
        if not args:
            await tg_send(chat_id, "❌ Укажите файлы: /data delete file1.xxx, file2.xxx")
            return JSONResponse({"ok": True})

        filenames = [f.strip() for f in args.split(",") if f.strip()]
        deleted, failed = [], []

        for filename in filenames:
            if data_storage.delete_file(filename):
                deleted.append(filename)
            else:
                failed.append(filename)

        msg = f"✓ Удалено: {len(deleted)}"
        if deleted:
            msg += f"\n{', '.join(deleted)}"
        if failed:
            msg += f"\n❌ Не найдены: {len(failed)}"

        await tg_send(chat_id, msg)
        return JSONResponse({"ok": True})

    if cmd_root == "/data" and tail_lower == "export all":
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