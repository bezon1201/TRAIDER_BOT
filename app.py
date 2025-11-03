import os
import json
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request

# ===== Optional helpers (soft imports). If module is missing, we fallback gracefully.
def _soft_import(attr_path: str):
    """
    Try to import "module:function_or_name". Return callable or object, or None.
    """
    try:
        mod_name, name = attr_path.split(":")
        mod = __import__(mod_name, fromlist=[name])
        return getattr(mod, name, None)
    except Exception:
        return None

# symbol card builder (required in our flows)
_build_symbol_message = _soft_import("symbol_info:build_symbol_message")

# budget command handler (new)
_handle_budget_command = _soft_import("budget:handle_budget_command")

# metrics runner (optional hooks)
_start_collector = _soft_import("metrics_runner:start_collector")
_stop_collector  = _soft_import("metrics_runner:stop_collector")

# Optional domain-specific formatters if you have them in your project
_build_market_message    = _soft_import("market_info:build_market_message")
_build_mode_message      = _soft_import("mode_info:build_mode_message")
_build_portfolio_message = _soft_import("portfolio_info:build_portfolio_message")
_build_coins_message     = _soft_import("pairs:build_coins_message")  # or any module you use

# ===== Telegram basics
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def _code(s: str) -> str:
    # Все сообщения в активном чате — через тройные одинарные кавычки, как вы просили.
    return f"'''\n{s}\n'''"

async def tg_send(chat_id: int, text: str) -> None:
    if not TELEGRAM_TOKEN:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        # We do not use parse_mode to avoid formatting surprises with quotes
        await client.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text
        })

# ===== Storage helpers
STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

def _coin_path(symbol: str) -> str:
    return os.path.join(STORAGE_DIR, f"{symbol}.json")

def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# ===== FastAPI app
app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True}

@app.post("/telegram")
async def telegram_webhook(request: Request):
    data = await request.json()
    message = data.get("message") or data.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True}

    text = (message.get("text") or "").strip()
    if not text:
        return {"ok": True}

    # ---- Normalization (case-insensitive commands and tickers)
    text_norm  = text
    text_lower = text_norm.lower()
    text_upper = text_norm.upper()

    # ---- /budget (case-insensitive)
    if text_lower.startswith("/budget"):
        if _handle_budget_command is None:
            await tg_send(chat_id, _code("Budget module missing"))
            return {"ok": True}
        reply = _handle_budget_command(text_norm)
        await tg_send(chat_id, _code(reply))
        return {"ok": True}

    # ---- /now (case-insensitive) – optional: trigger your collector if present
    if text_lower.startswith("/now"):
        # We only acknowledge; real collection is handled by your collector
        # If you want to trigger manually, uncomment:
        # if _start_collector: _start_collector()
        await tg_send(chat_id, _code("ok"))
        return {"ok": True}

    # ---- /market
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

    # ---- /mode
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

    # ---- /portfolio
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

    # ---- /coins
    if text_lower.startswith("/coins"):
        if _build_coins_message:
            try:
                msg = _build_coins_message(text_norm)
            except Exception as e:
                msg = f"coins: error {e}"
        else:
            # Fallback: read /data/pairs.json and list
            pairs_path = os.path.join(STORAGE_DIR, "pairs.json")
            pairs = _read_json(pairs_path) or {}
            arr = pairs if isinstance(pairs, list) else pairs.get("pairs") or pairs.get("symbols") or []
            if arr:
                msg = "Coins:\n" + "\n".join(str(s).upper() for s in arr)
            else:
                msg = "Coins: list is empty"
        await tg_send(chat_id, _code(msg))
        return {"ok": True}

    # ---- /json <SYMBOL> – dumps stored JSON
    if text_lower.startswith("/json"):
        parts = text_norm.split(maxsplit=1)
        if len(parts) == 1:
            await tg_send(chat_id, _code("Usage: /json SYMBOL"))
            return {"ok": True}
        sym = parts[1].strip().upper()
        path = _coin_path(sym)
        obj = _read_json(path)
        if obj is None:
            await tg_send(chat_id, _code(f"{sym}\nNo data"))
        else:
            # compact-ish, but readable
            await tg_send(chat_id, _code(f"{sym}\n" + json.dumps(obj, ensure_ascii=False, indent=2)))
        return {"ok": True}

    # ---- Symbol shortcut: /BTCUSDC, /ethusdc, etc (case-insensitive)
    if text_lower.startswith("/") and len(text_norm) > 2:
        sym = text_upper[1:].split()[0].upper()

        # Known service commands to ignore in this branch
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

    # Default: echo plain text in code block for safety
    await tg_send(chat_id, _code(text))
    return {"ok": True}
