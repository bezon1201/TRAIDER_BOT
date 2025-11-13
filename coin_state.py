
import os
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)

TF1 = os.environ.get("TF1", "12")
TF2 = os.environ.get("TF2", "6")

# Окно голосования по рынку в часах
try:
    MARKET_PUBLISH = int(os.environ.get("MARKET_PUBLISH", "24"))
except ValueError:
    MARKET_PUBLISH = 24


def _symbol_raw_path(symbol: str) -> Path:
    """Путь к сырьевому JSON по монете, например BNBUSDC.json."""
    return STORAGE_PATH / f"{symbol}.json"


def _raw_market_path(symbol: str) -> Path:
    """Путь к jsonl-логу рынка, например BNBUSDCraw_market.jsonl."""
    return STORAGE_PATH / f"{symbol}raw_market.jsonl"


def _symbol_state_path(symbol: str) -> Path:
    """Путь к файлу агрегированного состояния, например BNBUSDCstate.json."""
    return STORAGE_PATH / f"{symbol}state.json"


def _safe_load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_symbol_raw(symbol: str) -> Dict[str, Any]:
    """Загружает сырьевой файл SYMBOL.json (если есть)."""
    return _safe_load_json(_symbol_raw_path(symbol))


def read_raw_market_window(
    symbol: str,
    now_ts: Optional[int] = None,
    window_hours: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Читает SYMBOLraw_market.jsonl и возвращает записи за окно window_hours часов.
    Если файла нет или записей нет — вернётся пустой список.
    """
    if now_ts is None:
        now_ts = int(time.time())
    if window_hours is None:
        window_hours = MARKET_PUBLISH

    path = _raw_market_path(symbol)
    if not path.exists():
        return []

    cutoff = now_ts - int(window_hours) * 3600
    result: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = rec.get("ts")
                if not isinstance(ts, (int, float)):
                    continue
                if int(ts) >= cutoff:
                    result.append(rec)
    except Exception:
        return []

    return result


def aggregate_market_mode(records: List[Dict[str, Any]]) -> str:
    """
    Считает режим рынка по голосам:
    - записи с market_mode == "UP" → UP
    - market_mode == "DOWN" → DOWN
    - всё остальное → RANGE
    Если UP > 50% → UP; DOWN > 50% → DOWN; иначе RANGE.
    Если записей нет — RANGE.
    """
    if not records:
        return "RANGE"

    up = 0
    down = 0
    rng = 0

    for rec in records:
        mode = str(rec.get("market_mode", "")).upper()
        if mode == "UP":
            up += 1
        elif mode == "DOWN":
            down += 1
        else:
            rng += 1

    total = up + down + rng
    if total == 0:
        return "RANGE"

    if up > total * 0.5:
        return "UP"
    if down > total * 0.5:
        return "DOWN"
    return "RANGE"


def compute_market_mode(
    symbol: str,
    now_ts: Optional[int] = None,
    window_hours: Optional[int] = None,
) -> str:
    """Высчитывает итоговый режим рынка для символа за окно по логам raw_market."""
    records = read_raw_market_window(symbol, now_ts=now_ts, window_hours=window_hours)
    mode = aggregate_market_mode(records)
    return mode


def build_symbol_state(
    symbol: str,
    market_mode: str,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Собирает структуру для SYMBOLstate.json на основе:
    - SYMBOL.json (tf1/tf2, raw, trading_params)
    - рассчитанного market_mode.
    """
    if now_ts is None:
        now_ts = int(time.time())

    raw_data = load_symbol_raw(symbol)

    tf1 = raw_data.get("tf1", TF1)
    tf2 = raw_data.get("tf2", TF2)

    trading_params = raw_data.get("trading_params") or {}

    # пробуем достать MA30 и ATR14 из сигнала по TF1
    ma30_val = 0.0
    atr14_val = 0.0
    try:
        raw_block = (raw_data.get("raw") or {}).get(str(tf1))
        if isinstance(raw_block, dict):
            sig = raw_block.get("signal") or {}
            ma30_val = float(sig.get("ma30", 0.0))
            atr14_val = float(sig.get("atr14", 0.0))
    except Exception:
        # на всякий случай не падаем
        ma30_val = 0.0
        atr14_val = 0.0

    state: Dict[str, Any] = {
        "symbol": symbol,
        "tf1": tf1,
        "tf2": tf2,
        "updated_ts": now_ts,
        "market_mode": market_mode,
        "MA30": ma30_val,
        "ATR14": atr14_val,
        "trading_params": trading_params if isinstance(trading_params, dict) else {},
    }
    return state


def save_symbol_state(symbol: str, state: Dict[str, Any]) -> None:
    """Сохраняет SYMBOLstate.json."""
    path = _symbol_state_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def update_symbol_state(
    symbol: str,
    now_ts: Optional[int] = None,
    window_hours: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Полный цикл для одной монеты:
    - читает лог raw_market за окно,
    - считает режим рынка,
    - собирает state,
    - сохраняет SYMBOLstate.json,
    - возвращает state.
    """
    if now_ts is None:
        now_ts = int(time.time())
    mode = compute_market_mode(symbol, now_ts=now_ts, window_hours=window_hours)
    state = build_symbol_state(symbol, market_mode=mode, now_ts=now_ts)
    save_symbol_state(symbol, state)
    return state


def market_mode_only(
    symbol: str,
    now_ts: Optional[int] = None,
    window_hours: Optional[int] = None,
) -> str:
    """
    Утилита для /market: только режим рынка по логам, без обновления state-файла.
    """
    if now_ts is None:
        now_ts = int(time.time())
    return compute_market_mode(symbol, now_ts=now_ts, window_hours=window_hours)
