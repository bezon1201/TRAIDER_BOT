import asyncio
import logging
from binance.client import Client
import httpx

logger = logging.getLogger(__name__)

async def collect_all_metrics(storage_path: str, delay_ms: int = 50):
    from data import DataStorage
    from metrics import read_pairs
    from market_calculation import calculate_raw_markets

    data_storage = DataStorage(storage_path)
    pairs = read_pairs(storage_path)

    if not pairs:
        logger.warning("No pairs configured")
        return {}

    logger.info(f"Starting collection for {len(pairs)} pairs...")
    results = {}

    try:
        for symbol in pairs:
            try:
                ticker = httpx.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}").json()
                exchange_info = httpx.get(f"https://api.binance.com/api/v3/exchangeInfo?symbol={symbol}").json()

                klines_12h = httpx.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=12h&limit=30").json()
                klines_6h = httpx.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=6h&limit=30").json()
                klines_4h = httpx.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=4h&limit=30").json()
                klines_2h = httpx.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=2h&limit=30").json()

                metrics_data = {
                    "symbol": symbol,
                    "ticker": ticker,
                    "exchange_info": exchange_info,
                    "klines": {
                        "12h": klines_12h,
                        "6h": klines_6h,
                        "4h": klines_4h,
                        "2h": klines_2h
                    }
                }

                data_storage.save_file(f"{symbol}.json", metrics_data)
                calculate_raw_markets(storage_path, symbol)
                results[symbol] = True
                logger.info(f"✓ Metrics saved: {symbol}")

                await asyncio.sleep(delay_ms / 1000)
            except Exception as e:
                logger.error(f"Error collecting {symbol}: {e}")
                results[symbol] = False

        logger.info(f"Collection done: {sum(results.values())}/{len(pairs)} success")
    except Exception as e:
        logger.error(f"Collection error: {e}")

    return results
