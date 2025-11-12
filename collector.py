import logging
import asyncio
from typing import Dict, Any, List, Optional
import httpx
from datetime import datetime, timezone
from metrics import save_metrics, read_pairs
from indicators import calculate_indicators
from market_calculation import calculate_and_save_raw_markets

logger = logging.getLogger(__name__)

BINANCE_API = "https://api.binance.com/api/v3"
TIMEFRAMES = ["12h", "6h", "4h", "2h"]
KLINES_LIMIT = 30

_filters_cache = {}
_filters_cache_time = {}

async def fetch_ticker_price(client: httpx.AsyncClient, symbol: str) -> Optional[Dict[str, Any]]:
    try:
        response = await client.get(f"{BINANCE_API}/ticker/24hr", params={"symbol": symbol})
        if response.status_code == 200:
            data = response.json()
            return {
                "symbol": symbol,
                "price": float(data.get("lastPrice", 0)),
                "bid_price": float(data.get("bidPrice", 0)),
                "ask_price": float(data.get("askPrice", 0)),
                "high": float(data.get("highPrice", 0)),
                "low": float(data.get("lowPrice", 0)),
                "volume": float(data.get("volume", 0)),
                "quote_asset_volume": float(data.get("quoteAssetVolume", 0)),
                "trades": int(data.get("count", 0)),
            }
    except Exception as e:
        logger.error(f"Error fetching ticker {symbol}: {e}")
    return None

async def fetch_exchange_info(client: httpx.AsyncClient, symbol: str) -> Optional[Dict[str, Any]]:
    current_time = datetime.now(timezone.utc).timestamp()
    if symbol in _filters_cache and symbol in _filters_cache_time:
        if current_time - _filters_cache_time[symbol] < 21600:
            logger.debug(f"Cache: {symbol}")
            return _filters_cache[symbol]
    try:
        response = await client.get(f"{BINANCE_API}/exchangeInfo", params={"symbol": symbol})
        if response.status_code == 200:
            data = response.json()
            filters = {}
            for f in data.get("symbols", [{}])[0].get("filters", []):
                ft = f.get("filterType", "")
                if ft == "PRICE_FILTER":
                    filters["price_filter"] = {"min_price": float(f.get("minPrice", 0)), "max_price": float(f.get("maxPrice", 0)), "tick_size": float(f.get("tickSize", 0))}
                elif ft == "LOT_SIZE":
                    filters["lot_size"] = {"min_qty": float(f.get("minQty", 0)), "max_qty": float(f.get("maxQty", 0)), "step_size": float(f.get("stepSize", 0))}
                elif ft == "MIN_NOTIONAL":
                    filters["min_notional"] = {"min_notional": float(f.get("minNotional", 0))}
                elif ft == "MAX_NUM_ORDERS":
                    filters["max_num_orders"] = {"limit": int(f.get("maxNumOrders", 0))}
                elif ft == "MAX_NUM_ALGO_ORDERS":
                    filters["max_num_algo_orders"] = {"limit": int(f.get("maxNumAlgoOrders", 0))}
            _filters_cache[symbol] = filters
            _filters_cache_time[symbol] = current_time
            return filters
    except Exception as e:
        logger.error(f"Error fetching exchange info {symbol}: {e}")
    return None

async def fetch_klines(client: httpx.AsyncClient, symbol: str, interval: str, limit: int = KLINES_LIMIT) -> Optional[List[List[Any]]]:
    try:
        response = await client.get(f"{BINANCE_API}/klines", params={"symbol": symbol, "interval": interval, "limit": limit})
        return response.json() if response.status_code == 200 else None
    except Exception as e:
        logger.error(f"Error fetching klines {symbol} {interval}: {e}")
    return None

async def collect_metrics_for_symbol(client: httpx.AsyncClient, symbol: str, storage_dir: str) -> bool:
    try:
        ticker = await fetch_ticker_price(client, symbol)
        if not ticker:
            logger.warning(f"No ticker for {symbol}")
            return False
        filters = await fetch_exchange_info(client, symbol)
        timeframes_data = {}
        for tf in TIMEFRAMES:
            klines = await fetch_klines(client, symbol, tf, KLINES_LIMIT)
            if klines:
                indicators = calculate_indicators(klines)
                timeframes_data[tf] = {"klines": klines, "indicators": indicators}
            await asyncio.sleep(0.05)
        metrics = {"symbol": symbol, "ticker": ticker, "filters": filters, "timeframes": timeframes_data}
        success = save_metrics(storage_dir, symbol, metrics)
        if success:
            calculate_and_save_raw_markets(storage_dir, symbol, metrics)
        return success
    except Exception as e:
        logger.error(f"Error collecting {symbol}: {e}")
        return False

async def collect_all_metrics(storage_dir: str, delay_ms: int = 100) -> Dict[str, bool]:
    pairs = read_pairs(storage_dir)
    if not pairs:
        logger.warning("No pairs to collect")
        return {}
    logger.info(f"Starting collection for {len(pairs)} pairs...")
    results = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        for symbol in pairs:
            try:
                results[symbol] = await collect_metrics_for_symbol(client, symbol, storage_dir)
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)
            except Exception as e:
                logger.error(f"Error {symbol}: {e}")
                results[symbol] = False
    success_count = sum(1 for v in results.values() if v)
    logger.info(f"Collection done: {success_count}/{len(pairs)} success")
    return results
