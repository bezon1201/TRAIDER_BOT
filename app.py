
import os
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
import httpx

from portfolio import build_portfolio_message, adjust_invested_total
from metrics_runner import start_collector, stop_collector
from now_command import run_now
from range_mode import get_mode, set_mode, list_modes

BOT_TOKEN = os.getenv("TRAIDER_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("TRAIDER_ADMIN_CAHT_ID", "").strip()
WEBHOOK_BASE = os.getenv("TRAIDER_WEBHOOK_BASE") or os.getenv("WEBHOOK_BASE") or ""
METRIC_CHAT_ID = os.getenv("TRAIDER_METRIC_CHAT_ID", "").strip()
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

import json, re

# === Coins config helpers ===
def _pairs_env() -> list[str]:
    raw = os.getenv("PAIRS", "") or ""
    raw = raw.strip()
    if not raw:
        return []
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    # dedup preserving order
    seen=set(); out=[]
    for s in parts:
        if s not in seen:
            seen.add(s); out.append(s)
    return out

def load_pairs(storage_dir: str = STORAGE_DIR) -> list[str]:
    path = os.path.join(storage_dir, "pairs.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            res=[]; seen=set()
            for x in data:
                s = str(x).strip().upper()
                if s and s not in seen:
                    seen.add(s); res.append(s)
            return res
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return []
# === end helpers ===


TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
app = FastAPI()
client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

async def tg_send(chat_id: str, text: str) -> None:
    if not TELEGRAM_API:
        return
    try:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True},
        )
    except Exception:
        pass

async def _binance_ping() -> str:
    url = "https://api.binance.com/api/v3/ping"
    try:
        r = await client.get(url)
        return "✅" if r.status_code == 200 else f"❌ {r.status_code}"
    except Exception as e:
        return f"❌ {e.__class__.__name__}: {e}"

@app.on_event("startup")
async def on_startup():
    ping = await _binance_ping()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = f"{now_utc} Бот запущен\nBinance connection: {ping}"
    if ADMIN_CHAT_ID:
        await tg_send(ADMIN_CHAT_ID, msg)

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/telegram")
async def telegram_webhook(update: Request):
    try:
        data = await update.json()
    except Exception:
        data = {}
    message = data.get("message") or data.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    chat_id = str((message.get("chat") or {}).get("id") or "")
    if not chat_id:
        return {"ok": True}

    if text.startswith("/invested") or text.startswith("/invest "):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            raw = parts[1].replace(",", ".")
            try:
                delta = float(raw)
                new_total = adjust_invested_total(STORAGE_DIR, delta)
                sign = "+" if delta >= 0 else ""
                reply = f"OK. Added: {sign}{delta:.2f}$ | Invested total: {new_total:.2f}$"
            except ValueError:
                reply = "Нужна сумма: /invested 530 или /invest -10"
        else:
            reply = "Нужна сумма: /invested 530"
        await tg_send(chat_id, reply)
        return {"ok": True}

    
    if text.startswith("/coins"):
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            pairs = load_pairs()
            reply = "Пары: " + (", ".join(pairs) if pairs else "—")
            await tg_send(chat_id, reply)
            return {"ok": True}
        else:
            rest = parts[1].strip()
            items = [x.strip().upper() for x in rest.split() if x.strip()]
            valids = []
            invalids = []
            for sym in items:
                if re.fullmatch(r"[A-Z]+", sym) and sym.endswith("USDC"):
                    valids.append(sym)
                else:
                    invalids.append(sym)
            if invalids:
                await tg_send(chat_id, "Некорректные тикеры: " + ", ".join(invalids))
                return {"ok": True}
            # dedup
            seen=set(); filtered=[]
            for s in valids:
                if s not in seen:
                    seen.add(s); filtered.append(s)
            save_pairs(filtered)
            await tg_send(chat_id, "Пары обновлены: " + (", ".join(filtered) if filtered else "—"))
            return {"ok": True}

    if text.startswith("/now"):
        _, msg = await run_now()
        await tg_send(chat_id, msg)
        return {"ok": True}

    
    if text.startswith("/mode"):
        parts = text.split()
        # /mode
        if len(parts) == 1:
            summary = list_modes()
            await tg_send(chat_id, f"Режимы: {summary}")
            return {"ok": True}
        # /mode <SYMBOL>
        if len(parts) == 2:
            sym, md = get_mode(parts[1])
            if not sym:
                await tg_send(chat_id, "Некорректная команда")
                return {"ok": True}
            await tg_send(chat_id, f"{sym}: {md}")
            return {"ok": True}
        # /mode <SYMBOL> <LONG|SHORT>
        if len(parts) >= 3:
            sym = parts[1]
            md  = parts[2]
            try:
                sym, md = set_mode(sym, md)
                await tg_send(chat_id, f"{sym} → {md}")
            except ValueError:
                await tg_send(chat_id, "Некорректный режим")
            return {"ok": True}

    if text.startswith("/portfolio"):
        try:
            reply = await build_portfolio_message(client, BINANCE_API_KEY, BINANCE_API_SECRET, STORAGE_DIR)
        except Exception as e:
            reply = f"Ошибка портфеля: {e}"
        await tg_send(chat_id, reply or "Нет данных.")
        return {"ok": True}

    return {"ok": True}


@app.get("/")
async def root():
    return {"ok": True, "service": "traider-bot"}


@app.head("/")
async def root_head():
    return {"ok": True}


@app.head("/health")
async def health_head():
    return {"ok": True}


# metrics collector moved to metrics_runner.py


@app.on_event("startup")
async def _startup_metrics():
    # start metrics collector in background (jittered)
    await start_collector()

@app.on_event("shutdown")
async def _shutdown_metrics():
    await stop_collector()


# === Admin read-only endpoints ===
import glob

ADMIN_KEY = os.getenv("ADMIN_KEY", "").strip()

def _auth_ok(key: str) -> bool:
    return bool(ADMIN_KEY) and (key or "") == ADMIN_KEY

@app.get("/admin/files")
async def admin_files(key: str = ""):
    if not _auth_ok(key):
        return JSONResponse({"error":"forbidden"}, status_code=403)
    files = sorted([os.path.basename(p) for p in glob.glob(os.path.join(STORAGE_DIR, "*.json"))])
    return {"files": files}

@app.get("/admin/file")
async def admin_file(name: str = "", key: str = ""):
    if not _auth_ok(key):
        return JSONResponse({"error":"forbidden"}, status_code=403)
    safe = os.path.basename(name or "")
    if not safe.endswith(".json"):
        return JSONResponse({"error":"bad name"}, status_code=400)
    path = os.path.join(STORAGE_DIR, safe)
    if not os.path.exists(path):
        return JSONResponse({"error":"not found"}, status_code=404)
    return FileResponse(path, media_type="application/json", filename=safe)
# === end admin ===
