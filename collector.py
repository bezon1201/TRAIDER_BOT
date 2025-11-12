import os
import logging
import asyncio
from typing import Dict, Any, List, Optional
import httpx
from datetime import datetime, timezone
from metrics import save_metrics, read_pairs, normalize_pair
from indicators import calculate_indicators

logger = logging.getLogger(__name__)

BINANCE_API = "https://api.binance.com/api/v3"

TIMEFRAMES = ["12h", "6h", "4h", "2h"]

_filters_cache = {}
_filters_cache_time = {}

async def fetch_ticker_price(client: httpx.AsyncClient, symbol: str) -> Optional[Dict[str, Any]]:
    """Получает текущую цену для пары с Binance"""
    try:
        response = await client.get(
            f"{BINANCE_API}/ticker/24hr",
            params={"symbol": symbol}
        )

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
        else:
            logger.warning(f"Failed to fetch ticker for {symbol}: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"Error fetching ticker for {symbol}: {e}")
        return None

async def fetch_exchange_info(client: httpx.AsyncClient, symbol: str) -> Optional[Dict[str, Any]]:
    """Получает фильтры и ограничения пары с Binance (кешируется на 6 часов)"""
    current_time = datetime.now(timezone.utc).timestamp()

    if symbol in _filters_cache and symbol in _filters_cache_time:
        cache_age = current_time - _filters_cache_time[symbol]
        if cache_age < 21600:
            logger.debug(f"Using cached filters for {symbol}")
            return _filters_cache[symbol]

    try:
        response = await client.get(
            f"{BINANCE_API}/exchangeInfo",
            params={"symbol": symbol}
        )

        if response.status_code == 200:
            data = response.json()

            filters = {}
            for f in data.get("symbols", [{}])[0].get("filters", []):
                filter_type = f.get("filterType", "")

                if filter_type == "PRICE_FILTER":
                    filters["price_filter"] = {
                        "min_price": float(f.get("minPrice", 0)),
                        "max_price": float(f.get("maxPrice", 0)),
                        "tick_size": float(f.get("tickSize", 0)),
                    }

                elif filter_type == "LOT_SIZE":
                    filters["lot_size"] = {
                        "min_qty": float(f.get("minQty", 0)),
                        "max_qty": float(f.get("maxQty", 0)),
                        "step_size": float(f.get("stepSize", 0)),
                    }

                elif filter_type == "MIN_NOTIONAL":
                    filters["min_notional"] = {
                        "min_notional": float(f.get("minNotional", 0)),
                    }

                elif filter_type == "ICEBERG_PARTS":
                    filters["iceberg_parts"] = {
                        "limit": int(f.get("limit", 0)),
                    }

                elif filter_type == "MAX_NUM_ORDERS":
                    filters["max_num_orders"] = {
                        "limit": int(f.get("maxNumOrders", 0)),
                    }

                elif filter_type == "MAX_NUM_ALGO_ORDERS":
                    filters["max_num_algo_orders"] = {
                        "limit": int(f.get("maxNumAlgoOrders", 0)),
                    }

            _filters_cache[symbol] = filters
            _filters_cache_time[symbol] = current_time

            logger.info(f"Fetched and cached filters for {symbol}")
            return filters

        else:
            logger.warning(f"Failed to fetch exchange info for {symbol}: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"Error fetching exchange info for {symbol}: {e}")
        return None

async def fetch_klines(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str = "4h",
    limit: int = 100
) -> Optional[List[List[Any]]]:
    """Получает свечные данные (klines) с Binance"""
    try:
        response = await client.get(
            f"{BINANCE_API}/klines",
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": limit
            }
        )

        if response.status_code == 200:
            data = response.json()
            return data
        else:
            logger.warning(f"Failed to fetch klines for {symbol} ({interval}): {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"Error fetching klines for {symbol} ({interval}): {e}")
        return None

async def collect_metrics_for_symbol(
    client: httpx.AsyncClient,
    symbol: str,
    storage_dir: str
) -> bool:
    """Собирает все метрики для одной пары"""
    try:
        ticker = await fetch_ticker_price(client, symbol)
        if not ticker:
            logger.warning(f"Could not fetch ticker for {symbol}")
            return False

        filters = await fetch_exchange_info(client, symbol)

        timeframes_data = {}
        for tf in TIMEFRAMES:
            klines = await fetch_klines(client, symbol, tf, 100)

            if klines:
                indicators = calculate_indicators(klines)

                timeframes_data[tf] = {
                    "klines": klines,
                    "indicators": indicators,
                }

            await asyncio.sleep(0.05)

        metrics = {
            "symbol": symbol,
            "ticker": ticker,
            "filters": filters,
            "timeframes": timeframes_data,
        }

        success = save_metrics(storage_dir, symbol, metrics)

        if success:
            logger.info(f"Collected complete metrics for {symbol}")

        return success

    except Exception as e:
        logger.error(f"Error collecting metrics for {symbol}: {e}")
        return False

async def collect_all_metrics(storage_dir: str, delay_ms: int = 100) -> Dict[str, bool]:
    """Собирает метрики для всех пар из pairs.txt"""

    pairs = read_pairs(storage_dir)

    if not pairs:
        logger.warning("No pairs found in pairs.txt")
        return {}

    logger.info(f"Starting metrics collection for {len(pairs)} pairs")

    results = {}

    async with httpx.AsyncClient(timeout=15.0) as client:
        for symbol in pairs:
            try:
                success = await collect_metrics_for_symbol(client, symbol, storage_dir)
                results[symbol] = success

                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)

            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                results[symbol] = False

    success_count = sum(1 for v in results.values() if v)
    logger.info(f"Metrics collection completed: {success_count}/{len(pairs)} successful")

    return results
