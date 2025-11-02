
import os
from datetime import datetime, timezone
from fastapi import FastAPI, Request
import httpx

# --- Environment ---
BOT_TOKEN = os.getenv("TRAIDER_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("TRAIDER_ADMIN_CAHT_ID", "").strip()  # (typo preserved per spec)
WEBHOOK_BASE = os.getenv("TRAIDER_WEBHOOK_BASE") or os.getenv("WEBHOOK_BASE") or ""
METRIC_CHAT_ID = os.getenv("TRAIDER_METRIC_CHAT_ID", "").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI()

# Shared HTTP client (respects HTTP(S)_PROXY envs set on Render)
client = httpx.AsyncClient(timeout=10.0, follow_redirects=True)

async def _send_admin(text: str) -> None:
    if not (TELEGRAM_API and ADMIN_CHAT_ID):
        return
    try:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": ADMIN_CHAT_ID,
            "text": text
        })
    except Exception:
        # avoid crashing on startup if Telegram not reachable
        pass

async def _binance_ping() -> str:
    url = "https://api.binance.com/api/v3/ping"
    try:
        r = await client.get(url)
        ok = (r.status_code == 200)
        if ok:
            return "✅"
        else:
            return f"❌ {r.status_code}"
    except Exception as e:
        return f"❌ {e.__class__.__name__}: {e}"

@app.on_event("startup")
async def on_startup():
    # Binance connectivity check
    ping = await _binance_ping()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = f"{now_utc} Бот запущен\nBinance connection: {ping}"
    await _send_admin(msg)

@app.get("/health")
async def health():
    # GET will also satisfy HEAD checks in FastAPI
    return {"ok": True}

@app.post("/telegram")
async def telegram_webhook(update: Request):
    # Minimal stub to keep endpoint; do not change logic unless requested
    try:
        _ = await update.json()
    except Exception:
        _ = {}
    return {"ok": True}
