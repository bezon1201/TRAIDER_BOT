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
