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
from metric_scheduler import MetricScheduler

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
WEBHOOK_URL = os.getenv('WEBHOOK_BASE', '')
PORT = int(os.getenv('PORT', 10000))
DATA_STORAGE = os.getenv('DATA_STORAGE', '/data')

logger.info(f"Using DATA_STORAGE: {DATA_STORAGE}")

data_storage = DataStorage(DATA_STORAGE)
scheduler: MetricScheduler | None = None
scheduler_task: asyncio.Task | None = None

app = FastAPI()

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
FILE_API_BASE = f"https://api.telegram.org/file/bot{BOT_TOKEN}" if BOT_TOKEN else ""
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
        else:
            logger.error(f"sendMessage error: {response.status_code} {response.text}")
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
            logger.error(f"sendDocument error: {response.status_code} {response.text}")
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
    except Exception:
        data = {}

    message = data.get("message", {}) or {}
    # текст команды берём и из message["text"], и из caption у документа
    text = (message.get("text") or message.get("caption") or "").strip()
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")

    if not chat_id or not text:
        return JSONResponse({"ok": True})

    logger.info(f"Message from {chat_id}: {text[:50]}")

    lower_text = text.lower()
    parts = lower_text.split(maxsplit=1)
    cmd_token = parts[0] if parts else ""
    cmd_root = cmd_token.split("@", 1)[0]  # /data@bot -> /data
    tail_lower = parts[1].strip() if len(parts) > 1 else ""

    # === /start ===

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
            f"last_published: {config['last_published'][:19]}"
        )
        await tg_send(chat_id, msg)
        return JSONResponse({"ok": True})

    if cmd_root == "/scheduler" and tail_lower.startswith("period "):
        try:
            new_period = int(text.split()[-1])
            if scheduler.update_period(new_period):
                await tg_send(chat_id, f"✅ Период: {new_period} сек")
            else:
                await tg_send(chat_id, "❌ Диапазон: 900…86400 сек")
        except Exception:
            await tg_send(chat_id, "❌ Некорректное значение")
        return JSONResponse({"ok": True})

    if cmd_root == "/scheduler" and tail_lower.startswith("publish "):
        try:
            new_interval = int(text.split()[-1])
            if scheduler.update_publish_interval(new_interval):
                await tg_send(chat_id, f"✅ Публикация: {new_interval} часов")
            else:
                await tg_send(chat_id, "❌ Диапазон: 1…96 часов")
        except Exception:
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

    if lower_text.startswith("/coins"):
        action, pairs_list = parse_coins_command(text)

        if action == "list":
            all_pairs = read_pairs(DATA_STORAGE)
            if all_pairs:
                msg = f"📊 Активные пары ({len(all_pairs)}):\n" + ", ".join(all_pairs)
            else:
                msg = "📊 Список пар пуст"
            await tg_send(chat_id, msg)

        elif action == "delete":
            if not pairs_list:
                await tg_send(chat_id, "❌ Укажите пары для удаления")
                return JSONResponse({"ok": True})
            success, remaining = remove_pairs(DATA_STORAGE, pairs_list)
            if success:
                if remaining:
                    msg = (
                        f"✓ Пары обновлены ({len(remaining)}):\n"
                        + ", ".join(remaining)
                    )
                else:
                    msg = "✓ Все пары удалены, список пуст"
                await tg_send(chat_id, msg)
            else:
                await tg_send(chat_id, "❌ Ошибка")
        else:  # add
            if not pairs_list:
                await tg_send(chat_id, "❌ Укажите пары")
                return JSONResponse({"ok": True})
            success, all_pairs = add_pairs(DATA_STORAGE, pairs_list)
            if success:
                await tg_send(
                    chat_id,
                    f"✓ Пары обновлены ({len(all_pairs)}):\n" + ", ".join(all_pairs),
                )
            else:
                await tg_send(chat_id, "❌ Ошибка")

        return JSONResponse({"ok": True})

    # === СБОР МЕТРИК ===

    if lower_text == "/now":
        logger.info("Collecting metrics...")
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
            await tg_send(
                chat_id,
                "❌ Используйте: /market force 12+6 или /market force 4+2",
            )
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

    # импорт файла(ов) из сообщения: документ(ы) + caption "/data import"
    if cmd_root == "/data" and tail_lower.startswith("import"):
        # Telegram обычно присылает один document на сообщение
        docs: list[dict] = []
        doc = message.get("document")
        if doc:
            docs.append(doc)
        # на всякий случай поддержим неофициальные клиенты, если дадут список
        more_docs = message.get("documents") or []
        if isinstance(more_docs, list):
            docs.extend(d for d in more_docs if isinstance(d, dict))

        if not docs:
            await tg_send(
                chat_id,
                "❌
