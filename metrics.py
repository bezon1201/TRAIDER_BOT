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


def _safe_get(dct, *keys, default=None):
    cur = dct
    for k in keys:
        if cur is None or not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default

def _extract_ohlcv_lists(klines):
    highs, lows, closes = [], [], []
    for k in klines or []:
        try:
            highs.append(float(k[2]))
            lows.append(float(k[3]))
            closes.append(float(k[4]))
        except Exception:
            continue
    return highs, lows, closes

def _compute_ma_atr(klines, ma_period=30, atr_period=14):
    from indicators import calculate_sma, calculate_atr
    highs, lows, closes = _extract_ohlcv_lists(klines)
    ma = calculate_sma(closes, ma_period)
    atr = calculate_atr(highs, lows, closes, atr_period)
    return ma, atr

def _read_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def update_state_from_metrics(storage_dir: str, symbol: str, metrics: Dict[str, Any]) -> bool:
    """
    Writes ma30_12h, atr14_12h, ma30_6h, atr14_6h, price, tick_size, last_metrics_ts into <SYMBOL>.state.json.
    Keeps existing Mode / market_mode unchanged.
    """
    try:
        state_path = os.path.join(storage_dir, f"{symbol}.state.json")
        state = _read_state(state_path)

        # Pull klines for 12h and 6h
        tf = _safe_get(metrics, "timeframes", default={})
        kl_12h = _safe_get(tf, "12h", "klines", default=[])
        kl_6h  = _safe_get(tf, "6h",  "klines", default=[])

        ma30_12h, atr14_12h = _compute_ma_atr(kl_12h, 30, 14)
        ma30_6h,  atr14_6h  = _compute_ma_atr(kl_6h,  30, 14)

        price = float(_safe_get(metrics, "ticker", "lastPrice", default=_safe_get(metrics, "ticker", "last_price", default=0.0)) or 0.0)
        tick_size = float(_safe_get(metrics, "filters", "price_filter", "tick_size", default=0.0) or 0.0)

        # Update flat fields
        if ma30_12h is not None: state["ma30_12h"] = ma30_12h
        if atr14_12h is not None: state["atr14_12h"] = atr14_12h
        if ma30_6h is not None: state["ma30_6h"] = ma30_6h
        if atr14_6h is not None: state["atr14_6h"] = atr14_6h

        state["price"] = price
        if tick_size:
            state["tick_size"] = tick_size
        if "tick_size" not in state:
            state["tick_size"] = 0.01  # sane default

        # timestamps
        now_iso = datetime.now(timezone.utc).isoformat()
        state["last_metrics_ts"] = now_iso
        state["updated_at"] = now_iso

        _write_state(state_path, state)
        logger.info(f"✓ State updated from metrics: {symbol}")
        return True
    except Exception as e:
        logger.error(f"update_state_from_metrics error for {symbol}: {e}")
        return False

def save_metrics_and_update_state(storage_dir: str, symbol: str, metrics: Dict[str, Any]) -> bool:
    """Compat wrapper: write metrics json (as before) AND update state fields needed for step 1."""
    ok = save_metrics(storage_dir, symbol, metrics)
    try:
        update_state_from_metrics(storage_dir, symbol, metrics)
    except Exception as e:
        logger.error(f"state update failed (non-fatal): {e}")
    return ok


def _state_path(storage_dir: str, symbol: str) -> str:
    return os.path.join(storage_dir, f"{normalize_pair(symbol)}.state.json")

def read_state(storage_dir: str, symbol: str) -> Dict[str, Any]:
    try:
        with open(_state_path(storage_dir, symbol), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def write_state(storage_dir: str, symbol: str, state: Dict[str, Any]) -> None:
    path = _state_path(storage_dir, symbol)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def get_symbol_mode(storage_dir: str, symbol: str) -> str:
    """Return Mode from <SYMBOL>.state.json; default LONG if absent."""
    st = read_state(storage_dir, symbol)
    mode = str(st.get("Mode") or "LONG").upper()
    if mode not in ("LONG","SHORT"):
        mode = "LONG"
    return mode

def set_market_mode(storage_dir: str, symbol: str, market_mode: str) -> bool:
    """Set market_mode in <SYMBOL>.state.json; keep other fields."""
    try:
        st = read_state(storage_dir, symbol)
        st["market_mode"] = str(market_mode).upper()
        now_iso = datetime.now(timezone.utc).isoformat()
        st["updated_at"] = now_iso
        write_state(storage_dir, symbol, st)
        logger.info(f"✓ market_mode updated in state: {symbol} -> {st['market_mode']}")
        return True
    except Exception as e:
        logger.error(f"set_market_mode error for {symbol}: {e}")
        return False
