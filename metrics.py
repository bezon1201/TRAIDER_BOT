import logging
import re
from data import DataStorage

logger = logging.getLogger(__name__)

def read_pairs(storage_path: str):
    data_storage = DataStorage(storage_path)
    pairs_file = data_storage.read_file("pairs.txt")
    if pairs_file:
        return [p.strip().upper() for p in pairs_file.split(",") if p.strip()]
    return []

def write_pairs(storage_path: str, pairs: list):
    data_storage = DataStorage(storage_path)
    data_storage.save_file("pairs.txt", ",".join(pairs))

def add_pairs(storage_path: str, new_pairs: list):
    current_pairs = read_pairs(storage_path)
    new_pairs = [p.strip().upper() for p in new_pairs if p.strip()]
    all_pairs = list(set(current_pairs + new_pairs))
    write_pairs(storage_path, all_pairs)
    logger.info(f"✓ Pairs added: {len(all_pairs)} total")
    return True, all_pairs

def remove_pairs(storage_path: str, pairs_to_remove: list):
    current_pairs = read_pairs(storage_path)
    pairs_to_remove = [p.strip().upper() for p in pairs_to_remove]
    remaining = [p for p in current_pairs if p not in pairs_to_remove]
    write_pairs(storage_path, remaining)
    logger.info(f"✓ Pairs removed: {len(remaining)} remaining")
    return True, remaining

def parse_coins_command(text: str):
    text = text.lower().strip()
    if "delete" in text:
        match = re.search(r'delete\s+(.+)', text)
        if match:
            pairs = [p.strip() for p in match.group(1).split()]
            return 'delete', pairs
    elif text == "/coins":
        return 'list', []
    else:
        match = re.search(r'/coins\s+(.+)', text)
        if match:
            pairs = [p.strip() for p in match.group(1).split()]
            return 'add', pairs
    return 'list', []
