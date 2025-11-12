import logging
import json
from pathlib import Path

logger = logging.getLogger(__name__)

def calculate_raw_markets(storage_path: str, symbol: str):
    from data import DataStorage

    data_storage = DataStorage(storage_path)
    metrics = data_storage.read_file(f"{symbol}.json")

    if not metrics:
        return

    klines = metrics.get("klines", {})

    def extract_closes(kline_list):
        return [float(k[4]) for k in kline_list]

    closes_12h = extract_closes(klines.get("12h", []))
    closes_6h = extract_closes(klines.get("6h", []))
    closes_4h = extract_closes(klines.get("4h", []))
    closes_2h = extract_closes(klines.get("2h", []))

    raw_12_6 = {"closes_12h": closes_12h[-3:], "closes_6h": closes_6h[-3:]}
    raw_4_2 = {"closes_4h": closes_4h[-3:], "closes_2h": closes_2h[-3:]}

    file_path = Path(storage_path) / f"{symbol}_raw_market_12+6.jsonl"
    with open(file_path, 'w') as f:
        f.write(json.dumps(raw_12_6) + "\n")
    logger.info(f"✓ Raw market saved: {symbol}_raw_market_12+6.jsonl")

    file_path = Path(storage_path) / f"{symbol}_raw_market_4+2.jsonl"
    with open(file_path, 'w') as f:
        f.write(json.dumps(raw_4_2) + "\n")
    logger.info(f"✓ Raw market saved: {symbol}_raw_market_4+2.jsonl")

    logger.info(f"✓ Raw markets calculated for {symbol}")

def force_market_mode(storage_path: str, symbol: str, frame: str):
    calculate_raw_markets(storage_path, symbol)
    return f"✓ Market mode {frame} updated"
