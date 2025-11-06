# app.py — v13 + ETH/BNB sticker mappings

import os
import glob
import re
import json
from typing import Dict
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request

from portfolio import build_portfolio_message, adjust_invested_total
from now_command import run_now
from range_mode import get_mode, set_mode, list_modes
from symbol_info import build_symbol_message
from general_scheduler import (
    start_collector,
    stop_collector,
    scheduler_get_state,
    scheduler_set_enabled,
    scheduler_set_timing,
    scheduler_tail,
)

# === Sticker → Command mapping =================================================
# Поддерживаем и file_unique_id, и file_id для одного и того же стикера.
STICKER_TO_COMMAND: Dict[str, str] = {
    # BTC (из избранного)
    "AgADXXoAAmI4WEg": "/now btcusdc",
    "CAACAgIAAxkBAAE9cZBpC455Ia8n2PR-BoR6niG4gykRTAACXXoAAmI4WEg5O5Gu6FBfMzYE": "/now btcusdc",

    # BTC (из пака traider_crypto_bot / недавние)
    "AgADJogAAtfnYUg": "/now btcusdc",
    "CAACAgIAAxkBAAE9dPtpDAnY_j75m55h8ctPgwzLP4fy8gACJogAAtfnYUiiLR_pVyWZPTYE": "/now btcusdc",

    # ETH
    "AgADxokAAv_wWEg": "/now ethusdc",
    "CAACAgIAAxkBAAE9ddhpDCyOcuY8oEj0_mPe_E1zbEa-ogACxokAAv_wWEir8uUsEqgkvDYE": "/now ethusdc",

    # BNB
    "AgADJocAAka7YUg": "/now bnbusdc",
    "CAACAgIAAxkBAAE9djtpDD842Hiibb4OWsspe5QgYvQsgwACJocAAka7YUijem2oBO1AazYE": "/now bnbusdc",
}
# ==============================================================================

BOT_TOKEN = os.getenv("TRAIDER_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("TRAIDER_ADMIN_CAHT_ID", "").strip()   # оставляем как в окружении
WEBHOOK_BASE = os.getenv("TRAIDER_WEBHOOK_BASE") or os.getenv("WEBHOOK_BASE") or ""
METRIC_CHAT_ID = os.getenv("TRAIDER_METRIC_CHAT_ID", "").strip()
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
app = FastAPI()
client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)


def _log(*args):
    try:
        print("[bot]", *args, flush=True)
    except Exception:
        pass


def _code(msg: str) -> str:
    return f"""```
{msg}
```"""


def _load_json_safe(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _market_line_for(symbol: str) -> str:
    path = os.path.join(STORAGE_DIR, f"{symbol}.json")
    data = _load_json_safe(path)
    trade_mode = str((data.get("trade_mode") or "SHORT")).upper()
    market_mode = str((data.get("market_mode") or "RANGE")).upper()
    mm_emoji = {"UP": "⬆️", "DOWN": "⬇️", "RANGE": "🔄"}.get(market_mode, "🔄")
    tm_emoji = {"LONG": "📈", "SHORT": "📉"}.get(trade_mode, "")
    return f"{symbol} {market_mode}{mm_emoji} Mode {trade_mode}{tm_emoji}"


# === Coins config helpers ======================================================
def _pairs_env() -> list[str]:
    raw = os.getenv("PAIRS", "") or ""
    raw = raw.strip()
    if not raw:
        return []
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    seen = set()
    out = []
    for s in parts:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def load_pairs(storage_dir: str = STORAGE_DIR) -> list[str]:
    path = os.path.join(storage_dir, "pairs.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            res = []
            seen = set()
            for x in data:
                s = str(x).strip().upper()
                if s and s not in seen:
                    seen.add(s)
                    res.append(s)
            return res
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return []
# ==============================================================================


async def tg_send(chat_id: str, text: str) -> None:
    if not TELEGRAM_API:
        _log("tg_send SKIP: TELEGRAM_API missing")
        return
    head = (text or "").splitlines()[0] if text else ""
    _log("tg_send try: len=", len(text or ""), "parse=Markdown", "head=", head[:140])
    try:
        r = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
        )
        try:
            j = r.json()
        except Exception:
            j = None
        if r.status_code != 200 or (j and not j.get("ok", True)):
            _log("tg_send markdown resp:", r.status_code, j or r.text[:200])
            _log("tg_send fallback: plain text")
            r2 = await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
            try:
                j2 = r2.json()
            except Exception:
                j2 = None
            _log("tg_send plain resp:", r2.status_code, j2 or r2.text[:200])
        else:
            _log("tg_send ok:", r.status_code)
    except Exception as e:
        _log("tg_send exception:", e.__class__.__name__, str(e)[:240])


async def tg_send_file(chat_id: int, filepath: str, filename: str | None = None, caption: str | None = None):
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    _log("tg_send_file", filepath, "caption_len=", len(caption or ""))
    fn = filename or os.path.basename(filepath)
    try:
        async with httpx.AsyncClient(timeout=20.0) as _client:
            with open(filepath, "rb") as f:
                form = {"chat_id": str(chat_id)}
                files = {"document": (fn, f, "application/json")}
                if caption:
                    form["caption"] = caption
                r = await _client.post(api_url, data=form, files=files)
                r.raise_for_status()
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


@app.head("/")
async def root_head():
    return {"ok": True}


@app.head("/health")
async def health_head():
    return {"ok": True}


@app.get("/")
async def root():
    return {"ok": True, "service": "traider-bot"}


# --- Telegram webhook (основной) ---------------------------------------------
@app.post("/telegram")
async def telegram_webhook(update: Request):
    try:
        data = await update.json()
    except Exception:
        data = {}

    message = data.get("message") or data.get("edited_message") or {}
    text = (message.get("text") or message.get("caption") or "").strip()

    # Если это стикер — маппим в команду
    if not text and message.get("sticker"):
        st = message["sticker"]
        text = (
            STICKER_TO_COMMAND.get(st.get("file_unique_id"))
            or STICKER_TO_COMMAND.get(st.get("file_id"))
            or ""
        ).strip()

    text_norm = text
    text_lower = text_norm.lower()
    text_upper = text_norm.upper()
    chat_id = str((message.get("chat") or {}).get("id") or "")
    if not chat_id:
        return {"ok": True}

    # /invested | /invest
    if text_lower.startswith("/invested") or text_lower.startswith("/invest "):
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
        await tg_send(chat_id, _code(reply))
        return {"ok": True}

    # /coins
    if text_lower.startswith("/coins"):
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            pairs = load_pairs()
            reply = "Пары: " + (", ".join(pairs) if pairs else "—")
            await tg_send(chat_id, _code(reply))
            return {"ok": True}
        else:
            rest = parts[1].strip()
            items = [x.strip().upper() for x in rest.split() if x.strip()]
            valids, invalids = [], []
            for sym in items:
                if re.fullmatch(r"[A-Z]+", sym) and sym.endswith("USDC"):
                    valids.append(sym)
                else:
                    invalids.append(sym)
            if invalids:
                await tg_send(chat_id, _code("Некорректные тикеры: " + ", ".join(invalids)))
                return {"ok": True}
            seen, filtered = set(), []
            for s in valids:
                if s not in seen:
                    seen.add(s)
                    filtered.append(s)
            await tg_send(chat_id, _code("Пары обновлены: " + (", ".join(filtered) if filtered else "—")))
            return {"ok": True}

    # /now [<SYMBOL>] | /now long|short
    if text_lower.startswith("/now"):
        parts = text.strip().split()

        symbol_arg = None
        if len(parts) >= 2 and parts[1].lower() not in ("long", "short"):
            symbol_arg = parts[1].upper()

        mode_arg = None
        if len(parts) >= 2 and parts[1].strip().lower() in ("long", "short"):
            mode_arg = parts[1].strip().upper()

        count, msg = await run_now(symbol_arg)
        _log("/now result:", count)

        # Если указан тикер — отвечаем одной карточкой и выходим
        if symbol_arg:
            await tg_send(chat_id, _code(msg))
            return {"ok": True}

        # Иначе — общий апдейт + карточки по всем парам (с фильтром по режиму)
        await tg_send(chat_id, _code(msg))
        try:
            pairs = load_pairs()
        except Exception:
            pairs = []

        if mode_arg:
            try:
                filtered = []
                for _s in (pairs or []):
                    _, _m = get_mode(_s)
                    if _m == mode_arg:
                        filtered.append(_s)
                pairs = filtered
            except Exception:
                pass

        for sym in (pairs or []):
            try:
                smsg = build_symbol_message(sym)
                _log("/now symbol", sym, "len=", len(smsg or ""))
                await tg_send(chat_id, _code(smsg))
            except Exception:
                pass
        return {"ok": True}

    # /mode
    if text_lower.startswith("/mode"):
        parts = text.split()
        if len(parts) == 1:
            summary = list_modes()
            await tg_send(chat_id, _code(f"Режимы: {summary}"))
            return {"ok": True}
        if len(parts) == 2:
            sym, md = get_mode(parts[1])
            if not sym:
                await tg_send(chat_id, _code("Некорректная команда"))
                return {"ok": True}
            await tg_send(chat_id, _code(f"{sym}: {md}"))
            return {"ok": True}
        if len(parts) >= 3:
            sym = parts[1]
            md = parts[2]
            try:
                sym, md = set_mode(sym, md)
                await tg_send(chat_id, _code(f"{sym} → {md}"))
            except ValueError:
                await tg_send(chat_id, _code("Некорректный режим"))
            return {"ok": True}

    # /PORTFOLIO
    if text_lower.startswith("/portfolio"):
        try:
            reply = await build_portfolio_message(client, BINANCE_API_KEY, BINANCE_API_SECRET, STORAGE_DIR)
            _log("/portfolio built", "len=", len(reply or ""), "head=", (reply or "").splitlines()[0][:160])
        except Exception as e:
            reply = f"Ошибка портфеля: {e}"
        await tg_send(chat_id, reply or "Нет данных.")
        return {"ok": True}

    # Символьные шорткаты: /ETHUSDC, /BTCUSDC и т.п.
    if text_lower.startswith("/") and len(text_norm) > 2:
        sym = text_upper[1:].split()[0].upper()
        if sym not in ("NOW", "MODE", "PORTFOLIO", "COINS", "DATA", "JSON", "INVESTED", "INVEST", "MARKET", "SHEDULER"):
            msg = build_symbol_message(sym)
            await tg_send(chat_id, _code(msg))
            return {"ok": True}

    # /market
    if text_lower.startswith("/market"):
        parts = text.split()
        if len(parts) == 1:
            pairs = load_pairs()
            if not pairs:
                await tg_send(chat_id, _code("Пары: —"))
                return {"ok": True}
            lines = [_market_line_for(sym) for sym in pairs]
            await tg_send(chat_id, _code("\n".join(lines)))
            return {"ok": True}
        sym = parts[1].strip().upper()
        await tg_send(chat_id, _code(_market_line_for(sym)))
        return {"ok": True}

    # /data — список/удаление/отправка файла
    if text_lower.startswith("/data"):
        parts = text.split()
        if len(parts) == 1:
            files = sorted(
                [os.path.basename(p) for p in glob.glob(os.path.join(STORAGE_DIR, "*")) if os.path.isfile(p)]
            )
            msg = "Файлы: " + (", ".join(files) if files else "—")
            await tg_send(chat_id, _code(msg))
            return {"ok": True}
        if len(parts) >= 3 and parts[1].strip().lower() == "delete":
            name = os.path.basename(parts[2].strip())
            files = sorted(
                [os.path.basename(p) for p in glob.glob(os.path.join(STORAGE_DIR, "*")) if os.path.isfile(p)]
            )
            if name not in files:
                await tg_send(chat_id, _code("Файл не найден"))
                return {"ok": True}
            path = os.path.join(STORAGE_DIR, name)
            try:
                os.remove(path)
                await tg_send(chat_id, _code(f"Удалено: {name}"))
            except Exception as e:
                await tg_send(chat_id, _code(f"Ошибка удаления: {name}: {e.__class__.__name__}"))
            return {"ok": True}
        name = os.path.basename(parts[1].strip())
        path = os.path.join(STORAGE_DIR, name)
        if not (os.path.exists(path) and os.path.isfile(path)):
            await tg_send(chat_id, _code("Файл не найден"))
            return {"ok": True}
        await tg_send_file(chat_id, path, filename=name, caption=name)
        return {"ok": True}

    return {"ok": True}


# --- Alias webhook совместимый с /webhook/<token> -----------------------------
@app.post("/webhook/{token}")
async def telegram_webhook_alias(token: str, update: Request):
    expected = os.getenv("TRAIDER_BOT_TOKEN") or ""
    if expected and token != expected:
        # тихо подтверждаем, чтобы TG не спамил ретраями
        return {"ok": True, "description": "token mismatch"}
    return await telegram_webhook(update)


# --- Метрики (фоновый сборщик) -----------------------------------------------
@app.on_event("startup")
async def _startup_metrics():
    await start_collector()


@app.on_event("shutdown")
async def _shutdown_metrics():
    await stop_collector()
