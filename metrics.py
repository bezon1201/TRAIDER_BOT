import os
import json
import logging
from typing import List, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PAIRS_FILE = "pairs.txt"

def normalize_pair(pair: str) -> str:
    return str(pair).strip().upper()

def read_pairs(storage_dir: str) -> List[str]:
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

def remove_pairs(storage_dir: str, pairs_to_remove: List[str]) -> tuple[bool, List[str]]:
    try:
        existing = read_pairs(storage_dir)
        normalized_to_remove = set()
        for pair in pairs_to_remove:
            normalized_to_remove.add(normalize_pair(pair))
        remaining_pairs = [p for p in existing if p not in normalized_to_remove]
        write_pairs(storage_dir, remaining_pairs)
        logger.info(f"✓ Removed {len(existing) - len(remaining_pairs)} pairs")
        return True, remaining_pairs
    except Exception as e:
        logger.error(f"Error removing pairs: {e}")
        return False, []

def parse_coins_command(text: str) -> tuple[str, List[str]]:
    parts = text.strip().split()
    if parts and parts[0].lower() == '/coins':
        parts = parts[1:]
    if not parts:
        return 'list', []
    if parts[0].lower() == 'delete':
        return 'delete', [p.strip() for p in parts[1:] if p.strip()]
    else:
        return 'add', [p.strip() for p in parts if p.strip()]

def get_coin_file_path(storage_dir: str, symbol: str) -> str:
    os.makedirs(storage_dir, exist_ok=True)
    return os.path.join(storage_dir, f"{normalize_pair(symbol)}.json")

def save_metrics(storage_dir: str, symbol: str, metrics_data: Dict[str, Any]) -> bool:
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
    try:
        path = get_coin_file_path(storage_dir, symbol)
        if not os.path.exists(path):
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading metrics {symbol}: {e}")
        return {}




def set_coin_long_short(storage_dir: str, symbol: str, value: str) -> bool:
    """Set MODE to LONG or SHORT for a given coin JSON (always uppercase)."""
    try:
        value = str(value).upper()
        if value not in ("LONG", "SHORT"):
            return False
        path = get_coin_file_path(storage_dir, symbol)
        os.makedirs(storage_dir, exist_ok=True)
        data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data["symbol"] = normalize_pair(symbol)
        data["MODE"] = value
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error set MODE for {symbol}: {e}")
        return False
# --- Patched add_pairs to create default MODE on new coins ---
def add_pairs(storage_dir: str, pairs: List[str]) -> bool:
    """Add pairs to pairs.txt and ensure coin JSON exists with default MODE."""
    try:
        existing = set(read_pairs(storage_dir))
        to_add = [normalize_pair(p) for p in pairs if p]
        if not to_add:
            return True
        new = []
        for p in to_add:
            if p not in existing:
                new.append(p)
        # write file
        all_pairs = list(existing | set(new))
        all_pairs.sort()
        path = os.path.join(storage_dir, PAIRS_FILE)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(all_pairs))
        # ensure json defaults
        for p in new:
            ensure_coin_file_with_default(storage_dir, p)
        logger.info(f"Added pairs: {new}")
        return True
    except Exception as e:
        logger.error(f"Error adding pairs: {e}")
        return False
