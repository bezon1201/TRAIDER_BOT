import os
import logging
import asyncio
from typing import Dict, Any, List, Optional
import httpx
from datetime import datetime, timezone
from metrics import save_metrics, read_pairs, normalize_pair

logger = logging.getLogger(__name__)

BINANCE_API = "https://api.binance.com/api/v3"

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
            logger.warning(f"Failed to fetch klines for {symbol}: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"Error fetching klines for {symbol}: {e}")
        return None

async def collect_metrics_for_symbol(
    client: httpx.AsyncClient,
    symbol: str,
    storage_dir: str
) -> bool:
    """Собирает все метрики для одной пары"""
    try:
        # Получаем текущую цену
        ticker = await fetch_ticker_price(client, symbol)
        if not ticker:
            logger.warning(f"Could not fetch ticker for {symbol}")
            return False

        # Получаем свечные данные для разных интервалов
        klines_4h = await fetch_klines(client, symbol, "4h", 100)
        klines_1h = await fetch_klines(client, symbol, "1h", 100)
        klines_15m = await fetch_klines(client, symbol, "15m", 100)

        # Формируем полные метрики
        metrics = {
            "symbol": symbol,
            "ticker": ticker,
            "klines": {
                "4h": klines_4h,
                "1h": klines_1h,
                "15m": klines_15m,
            }
        }

        # Сохраняем в файл
        success = save_metrics(storage_dir, symbol, metrics)

        if success:
            logger.info(f"Collected metrics for {symbol}")

        return success

    except Exception as e:
        logger.error(f"Error collecting metrics for {symbol}: {e}")
        return False

async def collect_all_metrics(storage_dir: str, delay_ms: int = 100) -> Dict[str, bool]:
    """Собирает метрики для всех пар из pairs.txt"""

    # Читаем список пар
    pairs = read_pairs(storage_dir)

    if not pairs:
        logger.warning("No pairs found in pairs.txt")
        return {}

    logger.info(f"Starting metrics collection for {len(pairs)} pairs")

    results = {}

    # Используем AsyncClient для всех запросов
    async with httpx.AsyncClient(timeout=15.0) as client:
        for symbol in pairs:
            try:
                success = await collect_metrics_for_symbol(client, symbol, storage_dir)
                results[symbol] = success

                # Небольшая задержка между запросами
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)

            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                results[symbol] = False

    # Логируем итоги
    success_count = sum(1 for v in results.values() if v)
    logger.info(f"Metrics collection completed: {success_count}/{len(pairs)} successful")

    return results
