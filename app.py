import os
import json
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request

# ---------- soft imports ----------
def _soft_import(path: str):
    try:
        mod_name, name = path.split(":")
        mod = __import__(mod_name, fromlist=[name])
        return getattr(mod, name, None)
    except Exception:
        return None

_build_symbol_message   = _soft_import("symbol_info:build_symbol_message")
_handle_budget_command  = _soft_import("budget:handle_budget_command")

_build_market_message    = _soft_import("market_info:build_market_message")
_build_mode_message      = _soft_import("mode_info:build_mode_message")
_build_portfolio_message = _soft_import("portfolio_info:build_portfolio_message")
_build_coins_message     = _soft_import("pairs:build_coins_message")
_start_collector         = _soft_import("metrics_runner:start_collector")
_stop_collector          = _soft_import("metrics_runner:stop_collector")

# ---------- telegram ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_API   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def _code(s: str) -> str:
    # Все ответы — строго в тройных одинарных кавычках
    return f"'''\n{s}\n'''"

async def tg_send(chat_id: int, text: str) -> None:
    if not TELEGRAM_TOKEN:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})

# ---------- storage ----------
STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

def _coin_path(symbol: str) -> str:
    return os.path.join(STORAGE_DIR, f"{symbol}.json")

def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# ---------- app ----------
app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True}

@app.post("/telegram")
async def telegram_webhook(request: Request):
    payload = await request.json()
    message = payload.get("message") or payload.get("edited_message") or {}
    chat    = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True}

    text = (message.get("text") or "").strip()
    if not text:
        return {"ok": True}

    # case-insensitive
    text_norm  = text
    text_lower = text_norm.lower()
    text_upper = text_norm.upper()

    # ------- /budget -------
    if text_lower.startswith("/budget"):
        if _handle_budget_command is None:
            await tg_send(chat_id, _code("Budget module missing"))
            return {"ok": True}
        reply = _handle_budget_command(text_norm)
        await tg_send(chat_id, _code(reply))
        return {"ok": True}

    # ------- /now -------
    if text_lower.startswith("/now"):
        await tg_send(chat_id, _code("ok"))
        return {"ok": True}

    # ------- /market -------
    if text_lower.startswith("/market"):
        if _build_market_message:
            try:
                msg = _build_market_message(text_norm)
            except Exception as e:
                msg = f"market: error {e}"
        else:
            msg = "market: not available"
        await tg_send(chat_id, _code(msg))
        return {"ok": True}

    # ------- /mode -------
    if text_lower.startswith("/mode"):
        if _build_mode_message:
            try:
                msg = _build_mode_message(text_norm)
            except Exception as e:
                msg = f"mode: error {e}"
        else:
            msg = "mode: not available"
        await tg_send(chat_id, _code(msg))
        return {"ok": True}

    # ------- /portfolio -------
    if text_lower.startswith("/portfolio"):
        if _build_portfolio_message:
            try:
                msg = _build_portfolio_message(text_norm)
            except Exception as e:
                msg = f"portfolio: error {e}"
        else:
            msg = "portfolio: not available"
        await tg_send(chat_id, _code(msg))
        return {"ok": True}

    # ------- /coins -------
    if text_lower.startswith("/coins"):
        if _build_coins_message:
            try:
                msg = _build_coins_message(text_norm)
            except Exception as e:
                msg = f"coins: error {e}"
        else:
            pairs_path = os.path.join(STORAGE_DIR, "pairs.json")
            pairs = _read_json(pairs_path) or {}
            arr = pairs if isinstance(pairs, list) else pairs.get("pairs") or pairs.get("symbols") or []
            msg = "Coins:\n" + "\n".join(str(s).upper() for s in arr) if arr else "Coins: list is empty"
        await tg_send(chat_id, _code(msg))
        return {"ok": True}

    # ------- /json <SYMBOL> -------
    if text_lower.startswith("/json"):
        parts = text_norm.split(maxsplit=1)
        if len(parts) == 1:
            await tg_send(chat_id, _code("Usage: /json SYMBOL"))
            return {"ok": True}
        sym = parts[1].strip().upper()
        data = _read_json(_coin_path(sym))
        if data is None:
            await tg_send(chat_id, _code(f"{sym}\nNo data"))
        else:
            await tg_send(chat_id, _code(f"{sym}\n" + json.dumps(data, ensure_ascii=False, indent=2)))
        return {"ok": True}

    # ------- /BTCUSDC, /ethusdc etc -------
    if text_lower.startswith("/") and len(text_norm) > 2:
        sym = text_upper[1:].split()[0].upper()
        if sym not in ("NOW", "MODE", "PORTFOLIO", "COINS", "JSON", "INVESTED", "INVEST", "MARKET", "BUDGET"):
            if _build_symbol_message is None:
                await tg_send(chat_id, _code("symbol_info missing"))
                return {"ok": True}
            try:
                msg = _build_symbol_message(sym)
            except Exception as e:
                msg = f"{sym}\nerror: {e}"
            await tg_send(chat_id, _code(msg))
            return {"ok": True}

    await tg_send(chat_id, _code(text))
    return {"ok": True}
