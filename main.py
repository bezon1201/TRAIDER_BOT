from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
# -*- coding: utf-8 -*-
"""
main.py — Telegram bot command handlers (updated for version 1.1)
Variant 2: per-symbol LONG/SHORT bias selects the market-mode frame:
  LONG  -> 12+6
  SHORT -> 6+4
"""
import os
from typing import Optional

from data import load_symbol_json, save_symbol_json, get_storage_dir, list_symbols, get_symbol_bias
from market_calculation import force_market_mode, calculate_and_save_raw_markets, run_market_pipeline_by_bias
from collector import collect_all_metrics

def resolve_symbol_from_ctx(ctx) -> Optional[str]:
    # Placeholder; adjust to your context extraction logic
    args = ctx.args if hasattr(ctx, "args") else []
    if args:
        return args[0].upper()
    # Fallback to default/current symbol from config if needed
    return os.environ.get("DEFAULT_SYMBOL", None)

def cmd_coin(update, ctx):
    """
    /coin <symbol> long|short
    Sets bias in <SYMBOL>.json to LONG or SHORT.
    """
    args = getattr(ctx, "args", [])
    if len(args) < 2:
        update.message.reply_text("Usage:\n/coin <symbol> long|short")
        return
    symbol = args[0].upper()
    mode = args[1].strip().lower()
    bias = "LONG" if mode == "long" else "SHORT"
    storage = get_storage_dir()
    data = load_symbol_json(storage, symbol)
    if data is None:
        data = {"symbol": symbol}
    data["bias"] = bias
    save_symbol_json(storage, symbol, data)
    update.message.reply_text(f"{symbol} → bias={bias} (saved)")

def cmd_market_force(update, ctx):
    """
    /market force
    Uses the symbol's bias to choose the frame and update market_mode.
    """
    args = getattr(ctx, "args", [])
    symbol = args[0].upper() if args else os.environ.get("DEFAULT_SYMBOL", None)
    if not symbol:
        update.message.reply_text("Usage:\n/market force <symbol?>\n(If DEFAULT_SYMBOL is set, the argument is optional)")
        return
    storage = get_storage_dir()
    bias = get_symbol_bias(storage, symbol) or "LONG"
    frame = "12+6" if bias == "LONG" else "6+4"
    res = force_market_mode(storage, symbol, frame=frame)
    update.message.reply_text(f"market_mode updated for {symbol} using {frame} (bias={bias}): {res}")

def cmd_now(update, ctx):
    """
    /now <symbol?>
    Collects metrics only for the frames required by bias and runs raw + market_mode.
    """
    args = getattr(ctx, "args", [])
    symbol = args[0].upper() if args else os.environ.get("DEFAULT_SYMBOL", None)
    if not symbol:
        update.message.reply_text("Usage:\n/now <symbol?>\n(If DEFAULT_SYMBOL is set, the argument is optional)")
        return
    storage = get_storage_dir()
    bias = get_symbol_bias(storage, symbol) or "LONG"

    # Collect only required timeframes
    if bias == "LONG":
        collect_metrics(symbol, "12h")
        collect_metrics(symbol, "6h")
        calculate_and_save_raw_markets(storage, symbol, frame="12+6")
        force_market_mode(storage, symbol, frame="12+6")
        update.message.reply_text(f"{symbol}: collected 12h+6h, wrote raw 12+6, updated market_mode (bias=LONG)")
    else:
        collect_metrics(symbol, "6h")
        collect_metrics(symbol, "4h")
        calculate_and_save_raw_markets(storage, symbol, frame="6+4")
        force_market_mode(storage, symbol, frame="6+4")
        update.message.reply_text(f"{symbol}: collected 6h+4h, wrote raw 6+4, updated market_mode (bias=SHORT)")

# You should wire these handlers into your bot's dispatcher in your existing init code.
# Example with python-telegram-bot:
# dispatcher.add_handler(CommandHandler("coin", cmd_coin))
# dispatcher.add_handler(CommandHandler("market", cmd_market_force, filters=Filters.regex(r"^force\b")))
# dispatcher.add_handler(CommandHandler("now", cmd_now))


# ---- FastAPI ASGI app ----
app = FastAPI(title="Market Bot API", version="1.3")

class CoinBody(BaseModel):
    symbol: Optional[str] = None
    mode: Optional[str] = None  # "long" | "short"

class SymbolBody(BaseModel):
    symbol: Optional[str] = None

def _resolve_symbol(symbol: Optional[str]) -> str:
    sym = (symbol or os.environ.get("DEFAULT_SYMBOL", "")).upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required (body.symbol or DEFAULT_SYMBOL)")
    return sym

@app.get("/healthz")
def healthz():
    return {"ok": True, "version": "1.3"}

@app.post("/coin")
def set_coin(body: CoinBody):
    symbol = _resolve_symbol(body.symbol)
    mode = (body.mode or "long").lower()
    bias = "LONG" if mode == "long" else "SHORT"
    storage = get_storage_dir()
    data = load_symbol_json(storage, symbol) or {"symbol": symbol}
    data["bias"] = bias
    save_symbol_json(storage, symbol, data)
    return {"symbol": symbol, "bias": bias, "status": "saved"}

@app.post("/market/force")
def api_market_force(body: SymbolBody):
    symbol = _resolve_symbol(body.symbol)
    storage = get_storage_dir()
    bias = get_symbol_bias(storage, symbol) or "LONG"
    frame = "12+6" if bias == "LONG" else "6+4"
    mode = force_market_mode(storage, symbol, frame=frame)
    return {"symbol": symbol, "bias": bias, "frame": frame, "market_mode": mode}

@app.post("/now")
def api_now(body: SymbolBody):
    symbol = _resolve_symbol(body.symbol)
    storage = get_storage_dir()
    bias = get_symbol_bias(storage, symbol) or "LONG"
    # Full collection (collector decides details internally)
    collect_all_metrics(symbol)
    # Calculate raw + force mode by bias
    frame = "12+6" if bias == "LONG" else "6+4"
    calculate_and_save_raw_markets(storage, symbol, frame=frame)
    mode = force_market_mode(storage, symbol, frame=frame)
    return {"symbol": symbol, "bias": bias, "frame": frame, "market_mode": mode, "collected": True}


# ---- Telegram webhook support (since 1.4) ----
import httpx

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

def _tg_api_url(method: str) -> str:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env is not set")
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

async def _tg_send_message(chat_id: int, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return  # silently ignore in local dev
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(_tg_api_url("sendMessage"), json={"chat_id": chat_id, "text": text})

def _parse_command(text: str):
    # returns (cmd, args_list)
    if not text or not text.startswith("/"):
        return None, []
    parts = text.strip().split()
    cmd = parts[0].split("@")[0]  # strip @BotName
    args = parts[1:]
    return cmd.lower(), args

@app.post("/telegram/webhook")
async def telegram_webhook(update: dict):
    # Basic Telegram update handler
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = msg.get("text") or ""

    cmd, args = _parse_command(text)
    if not cmd:
        return {"ok": True}

    try:
        if cmd == "/now":
            symbol = args[0].upper() if args else os.environ.get("DEFAULT_SYMBOL", "")
            if not symbol:
                await _tg_send_message(chat_id, "Usage: /now <symbol>")
                return {"ok": True}
            storage = get_storage_dir()
            bias = get_symbol_bias(storage, symbol) or "LONG"
            collect_all_metrics(symbol)
            frame = "12+6" if bias == "LONG" else "6+4"
            calculate_and_save_raw_markets(storage, symbol, frame=frame)
            mode = force_market_mode(storage, symbol, frame=frame)
            await _tg_send_message(chat_id, f"{symbol}: collected, frame={frame}, market_mode={mode} (bias={bias})")
            return {"ok": True}

        if cmd == "/market":
            # allow "/market force <symbol?>" or "/market <symbol?>"
            if args and args[0].lower() == "force":
                args = args[1:]
            symbol = args[0].upper() if args else os.environ.get("DEFAULT_SYMBOL", "")
            if not symbol:
                await _tg_send_message(chat_id, "Usage: /market force <symbol?>")
                return {"ok": True}
            storage = get_storage_dir()
            bias = get_symbol_bias(storage, symbol) or "LONG"
            frame = "12+6" if bias == "LONG" else "6+4"
            mode = force_market_mode(storage, symbol, frame=frame)
            await _tg_send_message(chat_id, f"{symbol}: market_mode={mode} via {frame} (bias={bias})")
            return {"ok": True}

        if cmd == "/coin":
            # /coin <symbol> long|short
            if len(args) < 2:
                await _tg_send_message(chat_id, "Usage: /coin <symbol> long|short")
                return {"ok": True}
            symbol = args[0].upper()
            mode = args[1].lower()
            bias = "LONG" if mode == "long" else "SHORT"
            storage = get_storage_dir()
            data = load_symbol_json(storage, symbol) or {"symbol": symbol}
            data["bias"] = bias
            save_symbol_json(storage, symbol, data)
            await _tg_send_message(chat_id, f"{symbol} → bias={bias} (saved)")
            return {"ok": True}

        if cmd == "/coins":
            storage = get_storage_dir()
            syms = list_symbols(storage)
            lines = []
            for s in syms:
                lines.append(f"{s} — {get_symbol_bias(storage, s)}")
            await _tg_send_message(chat_id, "Pairs:\n" + ("\n".join(lines) if lines else "—"))
            return {"ok": True}

        if cmd == "/data":
            # /data <symbol?>
            symbol = args[0].upper() if args else os.environ.get("DEFAULT_SYMBOL", "")
            if not symbol:
                await _tg_send_message(chat_id, "Usage: /data <symbol>")
                return {"ok": True}
            storage = get_storage_dir()
            d = load_symbol_json(storage, symbol) or {}
            # short summary
            mm = d.get("market_mode", "—")
            bias = d.get("bias", "LONG")
            await _tg_send_message(chat_id, f"{symbol}: market_mode={mm}, bias={bias}")
            return {"ok": True}

        # Unknown command
        await _tg_send_message(chat_id, "Unknown command")
        return {"ok": True}
    except Exception as e:
        if chat_id:
            await _tg_send_message(chat_id, f"Error: {e}")
        return {"ok": False}

@app.post("/telegram/set_webhook")
def set_webhook():
    # Helper to register webhook with Telegram
    base = os.environ.get("WEBHOOK_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
    if not base:
        raise HTTPException(status_code=400, detail="Set WEBHOOK_BASE_URL or RENDER_EXTERNAL_URL env")
    url = base.rstrip("/") + "/telegram/webhook"
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN env is missing")
    r = httpx.post(_tg_api_url("setWebhook"), json={"url": url}, timeout=15)
    ok = r.json()
    return {"requested_url": url, "telegram_response": ok}
