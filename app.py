
import os
from datetime import datetime, timezone
from fastapi import FastAPI, Request
import json
import httpx

from portfolio import build_portfolio_message, adjust_invested_total
from metrics_runner import start_collector, stop_collector
from now_command import run_now
from range_mode import get_mode, set_mode, list_modes
from symbol_info import build_symbol_message

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
        await tg_send(chat_id, _code(reply))
        return {"ok": True}

    
    if text.startswith("/coins"):
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            pairs = load_pairs()
            reply = "Пары: " + (", ".join(pairs) if pairs else "—")
            await tg_send(chat_id, _code(reply))
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
                await tg_send(chat_id, _code("Некорректные тикеры: " + ", ".join(invalids)))
                return {"ok": True}
            # dedup
            seen=set(); filtered=[]
            for s in valids:
                if s not in seen:
                    seen.add(s); filtered.append(s)
            save_pairs(filtered)
            await tg_send(chat_id, _code("Пары обновлены: " + (", ".join(filtered) if filtered else "—")))
            return {"ok": True}

    if text.startswith("/now"):
        _, msg = await run_now()
        await tg_send(chat_id, _code(msg))
        return {"ok": True}

    
    if text.startswith("/mode"):
        parts = text.split()
        # /mode
        if len(parts) == 1:
            summary = list_modes()
            await tg_send(chat_id, _code(f"Режимы: {summary}"))
            return {"ok": True}
    # Symbol shortcut: /ETHUSDC, /BTCUSDC etc
    if text.startswith("/") and len(text) > 2:
        sym = text[1:].split()[0].upper()
        # ignore known command prefixes
        if sym not in ("NOW","MODE","PORTFOLIO","COINS","JSON","INVESTED","INVEST"):
            msg = build_symbol_message(sym)
            await tg_send(chat_id, _code(msg))
            return {"ok": True}

        # /mode <SYMBOL>
        if len(parts) == 2:
            sym, md = get_mode(parts[1])
            if not sym:
                await tg_send(chat_id, _code("Некорректная команда"))
                return {"ok": True}
            await tg_send(chat_id, _code(f"{sym}: {md}"))
            return {"ok": True}
        # /mode <SYMBOL> <LONG|SHORT>
        if len(parts) >= 3:
            sym = parts[1]
            md  = parts[2]
            try:
                sym, md = set_mode(sym, md)
                await tg_send(chat_id, _code(f"{sym} → {md}"))
            except ValueError:
                await tg_send(chat_id, _code("Некорректный режим"))
            return {"ok": True}

    
    if text.startswith("/market"):
        parts = text.split()
        # list all
        if len(parts) == 1:
            pairs = load_pairs()
            if not pairs:
                await tg_send(chat_id, _code("Пары: —"))
                return {"ok": True}
            lines = [_market_line_for(sym) for sym in pairs]
            await tg_send(chat_id, _code("\n".join(lines)))
            return {"ok": True}
        # specific symbol
        sym = parts[1].strip().upper()
        await tg_send(chat_id, _code(_market_line_for(sym)))
        return {"ok": True}

    
    if text.startswith("/json"):
        parts = text.split()
        # /json -> list all json files in STORAGE_DIR
        if len(parts) == 1:
            files = sorted([os.path.basename(p) for p in glob.glob(os.path.join(STORAGE_DIR, "*.json"))])
            msg = "Файлы: " + (", ".join(files) if files else "—")
            await tg_send(chat_id, _code(msg))
            return {"ok": True}
        # /json <PAIR> -> send /data/<PAIR>.json as document
        sym = parts[1].strip().upper()
        safe = f"{sym}.json" if not sym.endswith(".json") else os.path.basename(sym)
        path = os.path.join(STORAGE_DIR, safe)
        if not os.path.exists(path):
            await tg_send(chat_id, _code("Файл не найден"))
            return {"ok": True}
        await tg_send_file(chat_id, path, filename=safe, caption=safe)
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
    # emojis
    mm_emoji = {"UP":"⬆️","DOWN":"⬇️","RANGE":"🔄"}.get(market_mode, "🔄")
    tm_emoji = {"LONG":"📈","SHORT":"📉"}.get(trade_mode, "")
    return f"{symbol} {market_mode}{mm_emoji} Mode {trade_mode}{tm_emoji}"


def _code(msg: str) -> str:
    return f"""```
{msg}
```"""


import glob

async def tg_send_file(chat_id: int, filepath: str, filename: str | None = None, caption: str | None = None):
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    fn = filename or os.path.basename(filepath)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=20.0) as client:
            with open(filepath, "rb") as f:
                form = {"chat_id": str(chat_id)}
                files = {"document": (fn, f, "application/json")}
                if caption:
                    form["caption"] = caption
                r = await client.post(api_url, data=form, files=files)
                r.raise_for_status()
    except Exception:
        # silently ignore to avoid breaking webhook
        pass
