# -*- coding: utf-8 -*-
"""
metric_scheduler.py — scheduler that routes per-symbol by bias.
"""
import logging, time
from data import get_storage_dir, list_symbols, get_symbol_bias
from collector import collect_metrics
from market_calculation import calculate_and_save_raw_markets, force_market_mode

logger = logging.getLogger(__name__)

def run_scheduler_once(symbols=None):
    storage = get_storage_dir()
    if symbols is None:
        symbols = list_symbols(storage)
    for symbol in symbols:
        try:
            bias = get_symbol_bias(storage, symbol) or "LONG"
            if bias == "LONG":
                collect_metrics(symbol, "12h")
                collect_metrics(symbol, "6h")
                calculate_and_save_raw_markets(storage, symbol, frame="12+6")
                force_market_mode(storage, symbol, frame="12+6")
                logger.info(f"[{symbol}] bias=LONG: updated via 12+6")
            else:
                collect_metrics(symbol, "6h")
                collect_metrics(symbol, "4h")
                calculate_and_save_raw_markets(storage, symbol, frame="6+4")
                force_market_mode(storage, symbol, frame="6+4")
                logger.info(f"[{symbol}] bias=SHORT: updated via 6+4")
        except Exception as e:
            logger.exception(f"Scheduler error for {symbol}: {e}")

def start_scheduler_loop(interval_seconds=900):
    while True:
        run_scheduler_once()
        time.sleep(interval_seconds)
