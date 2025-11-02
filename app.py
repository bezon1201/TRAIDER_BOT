
import os
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict
import httpx
from fastapi import FastAPI, Response, Request

app = FastAPI()

# --- Utils ---
def utc_now_str() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%d.%m.%Y %H:%M UTC")

async def send_start_messages() -> None:
    token = os.getenv("TRAIDER_BOT_TOKEN", "").strip()
    admin_chat = os.getenv("TRAIDER_ADMIN_CAHT_ID", "").strip()  # как задано пользователем
    active_chat = os.getenv("TRAIDER_ACTIVE_CHAT_ID", "").strip()

    if not token or not admin_chat or not active_chat:
        # Не падаем, просто выходим
        return

    text = f"{utc_now_str()} Бот запущен"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payloads = [
        {"chat_id": admin_chat, "text": text},
        {"chat_id": active_chat, "text": text},
    ]

    timeout = httpx.Timeout(10.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [client.post(url, json=p) for p in payloads]
        try:
            await asyncio.gather(*tasks)
        except Exception:
            # Тихо игнорируем ошибки старта — каркас
            pass

# --- Routes ---
@app.api_route("/health", methods=["HEAD"])
async def health_head() -> Response:
    return Response(status_code=200)

@app.post("/telegram")
async def telegram_webhook(_: Request) -> Dict[str, Any]:
    # Каркас: просто 200 OK
    return {"ok": True}

# --- Startup ---
@app.on_event("startup")
async def on_startup() -> None:
    # Отправляем сообщения о запуске (не блокируя приложение)
    asyncio.create_task(send_start_messages())
