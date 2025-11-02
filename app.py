
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

async def send_telegram(token: str, payload: Dict[str, Any]) -> None:
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    timeout = httpx.Timeout(10.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            await client.post(url, json=payload)
        except Exception:
            pass  # каркас — не валимся

async def send_start_message_only_admin() -> None:
    token = os.getenv("TRAIDER_BOT_TOKEN", "").strip()
    admin_chat = os.getenv("TRAIDER_ADMIN_CAHT_ID", "").strip()  # как задано пользователем
    if not (token and admin_chat):
        return
    text = f"{utc_now_str()} Бот запущен"
    await send_telegram(token, {"chat_id": admin_chat, "text": text})

async def ensure_webhook() -> None:
    """Устанавливает webhook, если задан WEBHOOK_BASE или TRAIDER_WEBHOOK_BASE."""
    token = os.getenv("TRAIDER_BOT_TOKEN", "").strip()
    base = os.getenv("WEBHOOK_BASE", "").strip() or os.getenv("TRAIDER_WEBHOOK_BASE", "").strip()
    if not (token and base):
        return
    url = f"https://api.telegram.org/bot{token}/setWebhook"
    webhook_url = base.rstrip('/') + "/telegram"
    params = {"url": webhook_url, "drop_pending_updates": True}
    timeout = httpx.Timeout(10.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            await client.post(url, params=params)
        except Exception:
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
    asyncio.create_task(ensure_webhook())
    asyncio.create_task(send_start_message_only_admin())
