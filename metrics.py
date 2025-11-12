import os
import json
import logging
from typing import List, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PAIRS_FILE = "pairs.txt"

def normalize_pair(pair: str) -> str:
    """Нормализует пару"""
    return str(pair).strip().upper()

def read_pairs(storage_dir: str) -> List[str]:
    """Читает список пар"""
    try:
        path = os.path.join(storage_dir, PAIRS_FILE)
        if not os.path.exists(path):
            logger.info(f"Pairs file not found: {path}")
            return []

        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        pairs = []
        for line in lines:
            pair = normalize_pair(line.strip())
            if pair and pair not in pairs:
                pairs.append(pair)

        logger.info(f"✓ Read {len(pairs)} pairs")
        return pairs
    except Exception as e:
        logger.error(f"Error reading pairs: {e}")
        return []

def write_pairs(storage_dir: str, pairs: List[str]) -> bool:
    """Записывает пары (атомарно)"""
    try:
        os.makedirs(storage_dir, exist_ok=True)
        path = os.path.join(storage_dir, PAIRS_FILE)

        normalized = []
        seen = set()
        for pair in pairs:
            p = normalize_pair(pair)
            if p and p not in seen:
                normalized.append(p)
                seen.add(p)

        normalized.sort()

        tmp_path = path + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            for pair in normalized:
                f.write(pair + '\n')

        os.replace(tmp_path, path)
        logger.info(f"✓ Written {len(normalized)} pairs")
        return True
    except Exception as e:
        logger.error(f"Error writing pairs: {e}")
        return False

def add_pairs(storage_dir: str, new_pairs: List[str]) -> tuple[bool, List[str]]:
    """Добавляет пары"""
    try:
        existing = read_pairs(storage_dir)
        all_pairs = existing.copy()

        for pair in new_pairs:
            p = normalize_pair(pair)
            if p and p not in all_pairs:
                all_pairs.append(p)

        write_pairs(storage_dir, all_pairs)
        return True, all_pairs
    except Exception as e:
        logger.error(f"Error adding pairs: {e}")
        return False, []

def parse_coins_command(text: str) -> List[str]:
    """Парсит команду /coins"""
    parts = text.strip().split()
    if parts and parts[0].lower() == '/coins':
        parts = parts[1:]
    return [p.strip() for p in parts if p.strip()]

def get_coin_file_path(storage_dir: str, symbol: str) -> str:
    """Путь к файлу метрик"""
    os.makedirs(storage_dir, exist_ok=True)
    return os.path.join(storage_dir, f"{normalize_pair(symbol)}.json")

def save_metrics(storage_dir: str, symbol: str, metrics_data: Dict[str, Any]) -> bool:
    """Сохраняет метрики (атомарно)"""
    try:
        file_path = get_coin_file_path(storage_dir, symbol)
        metrics_data["timestamp"] = datetime.now(timezone.utc).isoformat()

        tmp_path = file_path + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(metrics_data, f, indent=2, ensure_ascii=False)

        os.replace(tmp_path, file_path)
        logger.info(f"✓ Metrics saved: {symbol}")
        return True
    except Exception as e:
        logger.error(f"Error saving metrics {symbol}: {e}")
        return False

def read_metrics(storage_dir: str, symbol: str) -> Dict[str, Any]:
    """Читает метрики"""
    try:
        path = get_coin_file_path(storage_dir, symbol)
        if not os.path.exists(path):
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading metrics {symbol}: {e}")
        return {}
