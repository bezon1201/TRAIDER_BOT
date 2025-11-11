import os
import asyncio

import httpx
from fastapi import FastAPI, Request, Response, status

app = FastAPI()


TELEGRAM_API_BASE = "https://api.telegram.org"


def get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is not None:
        value = value.strip()
    return value or default


BOT_TOKEN = get_env("TRAIDER_BOT_TOKEN")
ADMIN_CHAT_ID = get_env("TRAIDER_ADMIN_CAHT_ID")
ADMIN_KEY = get_env("ADMIN_KEY")
WEBHOOK_BASE = get_env("WEBHOOK_BASE")
STORAGE_DIR = get_env("STORAGE_DIR", "/mnt/data")


async def send_telegram_message(chat_id: str, text: str) -> None:
    if not BOT_TOKEN or not chat_id:
        return

    url = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            await client.post(url, json=payload)
        except Exception:
            # На каркасе просто глотаем ошибку,
            # позже добавим нормальный логгер
            pass


async def set_webhook() -> None:
    if not BOT_TOKEN or not WEBHOOK_BASE:
        return

    url = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/setWebhook"
    webhook_url = WEBHOOK_BASE.rstrip("/") + "/webhook"

    payload = {
        "url": webhook_url,
        "allowed_updates": ["message", "callback_query"],
    }
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            await client.post(url, json=payload)
        except Exception:
            pass


@app.on_event("startup")
async def on_startup() -> None:
    # Создаём директорию хранения, если её нет
    if STORAGE_DIR:
        try:
            os.makedirs(STORAGE_DIR, exist_ok=True)
        except Exception:
            pass

    # Настройка вебхука и уведомление админа
    await set_webhook()

    if ADMIN_CHAT_ID:
        text = "🤖 Trader bot skeleton started."
        if ADMIN_KEY:
            text += f" Admin key: {ADMIN_KEY}"
        await send_telegram_message(ADMIN_CHAT_ID, text)


@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
async def healthcheck() -> Response:
    # Для uptime-проверок (HEAD/GET /)
    return Response(status_code=status.HTTP_200_OK, content="ok")


@app.post("/webhook", include_in_schema=False)
async def telegram_webhook(request: Request) -> Response:
    # Пока просто принимаем апдейты и отвечаем 200,
    # логика разбора команд появится позже.
    try:
        _update = await request.json()
        # Здесь позже появится разбор /now, /data и т.д.
    except Exception:
        pass
    return Response(status_code=status.HTTP_200_OK)
