import os
import json
import logging
from pathlib import Path
from typing import List, Set, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PAIRS_FILE = "pairs.txt"

def get_pairs_file_path(storage_dir: str) -> str:
    """Получает полный путь к файлу pairs.txt"""
    return os.path.join(storage_dir, PAIRS_FILE)

def normalize_pair(pair: str) -> str:
    """Нормализует пару: верхний регистр и удаляет пробелы"""
    return str(pair).strip().upper()

def read_pairs(storage_dir: str) -> List[str]:
    """Читает список пар из файла pairs.txt"""
    try:
        pairs_path = get_pairs_file_path(storage_dir)
        if not os.path.exists(pairs_path):
            logger.info(f"Pairs file not found: {pairs_path}")
            return []

        with open(pairs_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        pairs = []
        for line in lines:
            pair = normalize_pair(line.strip())
            if pair and pair not in pairs:
                pairs.append(pair)

        logger.info(f"Read {len(pairs)} pairs from {pairs_path}")
        return pairs

    except Exception as e:
        logger.error(f"Error reading pairs: {e}")
        return []

def write_pairs(storage_dir: str, pairs: List[str]) -> bool:
    """Записывает список пар в файл pairs.txt"""
    try:
        os.makedirs(storage_dir, exist_ok=True)
        pairs_path = get_pairs_file_path(storage_dir)

        # Нормализация и удаление дубликатов
        normalized = []
        seen = set()
        for pair in pairs:
            normalized_pair = normalize_pair(pair)
            if normalized_pair and normalized_pair not in seen:
                normalized.append(normalized_pair)
                seen.add(normalized_pair)

        # Сортировка для консистентности
        normalized.sort()

        with open(pairs_path, 'w', encoding='utf-8') as f:
            for pair in normalized:
                f.write(pair + '\n')

        logger.info(f"Written {len(normalized)} pairs to {pairs_path}")
        return True

    except Exception as e:
        logger.error(f"Error writing pairs: {e}")
        return False

def add_pairs(storage_dir: str, new_pairs: List[str]) -> tuple[bool, List[str]]:
    """Добавляет новые пары к существующему списку"""
    try:
        # Читаем существующие пары
        existing_pairs = read_pairs(storage_dir)

        # Объединяем и удаляем дубликаты
        all_pairs = existing_pairs.copy()
        added_count = 0

        for new_pair in new_pairs:
            normalized = normalize_pair(new_pair)
            if normalized and normalized not in all_pairs:
                all_pairs.append(normalized)
                added_count += 1

        # Записываем обратно
        write_pairs(storage_dir, all_pairs)

        return True, all_pairs

    except Exception as e:
        logger.error(f"Error adding pairs: {e}")
        return False, []

def parse_coins_command(command_text: str) -> List[str]:
    """Парсит команду /coins и извлекает пары"""
    parts = command_text.strip().split()

    if parts and parts[0].lower() == '/coins':
        parts = parts[1:]

    pairs = [p.strip() for p in parts if p.strip()]

    return pairs

def get_coin_file_path(storage_dir: str, symbol: str) -> str:
    """Получает путь к файлу монеты (например BTCUSDT.json)"""
    os.makedirs(storage_dir, exist_ok=True)
    return os.path.join(storage_dir, f"{normalize_pair(symbol)}.json")

def save_metrics(storage_dir: str, symbol: str, metrics_data: Dict[str, Any]) -> bool:
    """Сохраняет метрики монеты в JSON файл"""
    try:
        file_path = get_coin_file_path(storage_dir, symbol)

        # Добавляем временную метку
        metrics_data["timestamp"] = datetime.now(timezone.utc).isoformat()

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(metrics_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Metrics saved for {symbol}")
        return True

    except Exception as e:
        logger.error(f"Error saving metrics for {symbol}: {e}")
        return False

def read_metrics(storage_dir: str, symbol: str) -> Dict[str, Any]:
    """Читает метрики монеты из JSON файла"""
    try:
        file_path = get_coin_file_path(storage_dir, symbol)

        if not os.path.exists(file_path):
            return {}

        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return data

    except Exception as e:
        logger.error(f"Error reading metrics for {symbol}: {e}")
        return {}
