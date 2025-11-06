# app.py
import os
import json
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

# ==== Config ==================================================================

BOT_TOKEN = os.environ.get("TRAIDER_BOT_TOKEN", "").strip()
STORAGE_DIR = os.environ.get("STORAGE_DIR", "/data")

HTTP_PROXY = os.environ.get("HTTP_PROXY")
HTTPS_PROXY = os.environ.get("HTTPS_PROXY")

assert BOT_TOKEN, "TRAIDER_BOT_TOKEN must be set"

# ==== External helpers from project ==========================================

# Формирование карточки по символу
try:
    from symbol_info import build_symbol_message  # type: ignore
except Exception:
    def build_symbol_message(symbol: str) -> str:
        p = os.path.join(STORAGE_DIR, f"{symbol.upper()}.json")
        if not os.path.exists(p):
            return f"{symbol.upper()}\n(no data)"
        try:
            data = json.load(open(p, "r", encoding="utf-8"))
        except Exception:
            return f"{symbol.upper()}\n(bad data)"
        price = data.get("price") or data.get("Price") or "—"
        mode = (data.get("mode") or data.get("trade_mode") or "").upper()
        trend = (data.get("trend") or data.get("RANGE") or "RANGE").upper()
        lines = [symbol.upper(), f"Price {price}$ {trend} {mode}"]
        return "\n".join(lines)

# Обновление данных (реальная имплементация в now_command)
try:
    from now_command import run_now as _run_now_impl  # type: ignore
except Exception:
    async def _run_now_impl(symbol: Optional[str] = None):
        # заглушка
        return 0, "no-op"


# ==== FastAPI =================================================================

app = FastAPI()


# ==== Utils ===================================================================

def _log(*args: Any) -> None:
    print("[bot]", *args, flush=True)


def _code(msg: str) -> str:
    return "```\n" + str(msg) + "\n```"


async def tg_send(chat_id: int | str, text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    proxies = {}
    if HTTP_PROXY:
        proxies["http://"] = HTTP_PROXY
    if HTTPS_PROXY:
        proxies["https://"] = HTTPS_PROXY

    async with httpx.AsyncClient(proxies=proxies, timeout=20) as client:
        _log("tg_send try: len=", len(text), "parse=Markdown", "head=", text[:30].replace("\n", " "))
        r = await client.post(url, data=params)
        _log("tg_send ok:", r.status_code)


def _pairs_path() -> str:
    return os.path.join(STORAGE_DIR, "pairs.json")


def load_pairs() -> List[str]:
    p = _pairs_path()
    if not os.path.exists(p):
        return ["BTCUSDC", "ETHUSDC", "BNBUSDC"]
    try:
        data = json.load(open(p, "r", encoding="utf-8"))
        if isinstance(data, list) and data:
            return [str(x).upper() for x in data]
    except Exception:
        pass
    return ["BTCUSDC", "ETHUSDC", "BNBUSDC"]


# ==== Sticker → Command mapping ===============================================

# Используем сначала file_unique_id (стабильный), при необходимости — file_id.
STICKER_TO_COMMAND: Dict[str, str] = {
    # BTC стикер (из «избранного»)
    "AgADXXoAAmI4WEg": "/now btcusdc",
    "CAACAgIAAxkBAAE9cZBpC455Ia8n2PR-BoR6niG4gykRTAACXXoAAmI4WEg5O5Gu6FBfMzYE": "/now btcusdc",

    # BTC стикер (из пака traider_crypto_bot / недавние)
    "AgADJogAAtfnYUg": "/now btcusdc",
    "CAACAgIAAxkBAAE9dPtpDAnY_j75m55h8ctPgwzLP4fy8gACJogAAtfnYUiiLR_pVyWZPTYE": "/now btcusdc",
}


def _resolve_text_from_message(message: Dict[str, Any]) -> str:
    # обычный текст/подпись
    text = (message.get("text") or message.get("caption") or "").strip()
    if text:
        return text

    # если это стикер — маппим в команду
    st = message.get("sticker")
    if not st:
        return ""
    mapped = STICKER_TO_COMMAND.get(st.get("file_unique_id")) or STICKER_TO_COMMAND.get(st.get("file_id"))
    return (mapped or "").strip()


# ==== Commands ================================================================

async def run_now(symbol: Optional[str] = None):
    return await _run_now_impl(symbol)


# ==== Routes ==================================================================

@app.get("/", response_class=PlainTextResponse)
async def root() -> str:
    return "ok"


@app.head("/", response_class=PlainTextResponse)
async def root_head() -> str:
    return "ok"


@app.post(f"/webhook/{{token}}")
async def telegram_webhook(token: str, request: Request):
    if token != BOT_TOKEN:
        return JSONResponse({"ok": False, "error": "bad token"}, status_code=403)

    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": True})

    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    if not chat_id:
        return {"ok": True}

    text = _resolve_text_from_message(message)
    text_lower = (text or "").casefold()

    # /start
    if text_lower.startswith("/start"):
        await tg_send(chat_id, _code("бот на связи"))
        return {"ok": True}

    # /now [symbol] [long|short]
    if text_lower.startswith("/now"):
        parts = (text or "").split()
        symbol_arg: Optional[str] = None
        # если после /now идёт не режим — считаем это символом
        if len(parts) >= 2 and parts[1].lower() not in ("long", "short"):
            symbol_arg = parts[1].upper()

        count, msg = await run_now(symbol_arg)
        _log("/now result:", count)

        if symbol_arg:
            # точечный апдейт — одна карточка и выходим
            await tg_send(chat_id, _code(msg))
            return {"ok": True}

        # без аргумента — как раньше: summary + карточки по всем
        await tg_send(chat_id, _code(f"Обновлено: {count}"))
        for sym in load_pairs():
            try:
                await tg_send(chat_id, _code(build_symbol_message(sym)))
            except Exception as e:
                _log("send card error:", sym, e)
        return {"ok": True}

    # точечные команды символов (вывод карточки из файла)
    if text_lower.startswith("/btcusdc"):
        await tg_send(chat_id, _code(build_symbol_message("BTCUSDC")))
        return {"ok": True}
    if text_lower.startswith("/ethusdc"):
        await tg_send(chat_id, _code(build_symbol_message("ETHUSDC")))
        return {"ok": True}
    if text_lower.startswith("/bnbusdc"):
        await tg_send(chat_id, _code(build_symbol_message("BNBUSDC")))
        return {"ok": True}

    # прочее — игнор
    return {"ok": True}


# альтернативный URL тем же хендлером, если где-то используется
@app.post(f"/webhook/{{token}}/alias")
async def telegram_webhook_alias(token: str, request: Request):
    return await telegram_webhook(token, request)
