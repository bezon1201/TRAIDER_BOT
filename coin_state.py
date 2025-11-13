import os
import json
import time
from pathlib import Path
from typing import Any, Dict, List

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)

# Окно агрегации голосов для /market и /market force, часов
try:
    MARKET_PUBLISH = int(os.environ.get("MARKET_PUBLISH", "24"))
except ValueError:
    MARKET_PUBLISH = 24


def _raw_market_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}raw_market.jsonl"


def _symbol_raw_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}.json"


def _state_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}state.json"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def calc_market_mode_for_symbol(symbol: str, now_ts: int | None = None) -> str:
    """
    Считает режим рынка (UP / DOWN / RANGE) по логам SYMBOLraw_market.jsonl
    за последние MARKET_PUBLISH часов.
    """
    symbol = (symbol or "").upper()
    if not symbol:
        return "RANGE"

    path = _raw_market_path(symbol)
    if now_ts is None:
        now_ts = int(time.time())
    window_start = now_ts - MARKET_PUBLISH * 3600

    if not path.exists():
        return "RANGE"

    up = down = rng = 0

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
                if ts < window_start:
                    continue
                mode = str(rec.get("market_mode", "RANGE")).upper()
                if mode == "UP":
                    up += 1
                elif mode == "DOWN":
                    down += 1
                else:
                    rng += 1
    except Exception:
        # Если не удалось прочитать файл — считаем, что информации нет
        return "RANGE"

    total = up + down + rng
    if total == 0:
        return "RANGE"

    if up / total > 0.5:
        return "UP"
    if down / total > 0.5:
        return "DOWN"
    return "RANGE"


def _normalize_trading_params_for_state(trading_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Подготавливает trading_params для state:
    - дублирует корректный min_notional в symbol_info.min_notional из filters.NOTIONAL;
    - добавляет float-дубли числовых полей фильтров (*_f).
    """
    if not isinstance(trading_params, dict):
        trading_params = {}

    # Глубокие копии на уровне верхних словарей, чтобы не портить исходные структуры
    result: Dict[str, Any] = {}
    for key, value in trading_params.items():
        if isinstance(value, dict):
            result[key] = dict(value)
        else:
            result[key] = value

    symbol_info = dict(result.get("symbol_info") or {})
    filters = dict(result.get("filters") or {})

    # min_notional из NOTIONAL.minNotional
    notional = filters.get("NOTIONAL")
    min_notional_val: float = 0.0
    if isinstance(notional, dict):
        min_notional_val = _safe_float(notional.get("minNotional"), 0.0)
    else:
        # fallback — вдруг старые данные с уже записанным min_notional
        min_notional_val = _safe_float(symbol_info.get("min_notional", 0.0), 0.0)

    symbol_info["min_notional"] = float(min_notional_val)

    # Добавляем float-дубли для числовых строк во всех фильтрах
    for fname, fdata in list(filters.items()):
        if not isinstance(fdata, dict):
            continue
        f_copy = dict(fdata)
        for k, v in fdata.items():
            if isinstance(v, str):
                fv = _safe_float(v, None)
                if fv is not None:
                    f_copy[f"{k}_f"] = fv
        filters[fname] = f_copy

    result["symbol_info"] = symbol_info
    result["filters"] = filters
    return result


def recalc_state_for_symbol(symbol: str, now_ts: int | None = None) -> Dict[str, Any]:
    """
    Пересчитывает state для одной пары:
    - считает market_mode по логам raw_market;
    - берёт tf1/tf2, MA30/ATR14 и trading_params из сырьевого SYMBOL.json;
    - нормализует trading_params под state (min_notional и *_f);
    - сохраняет SYMBOLstate.json и возвращает словарь.
    """
    symbol = (symbol or "").upper()
    if not symbol:
        return {}

    if now_ts is None:
        now_ts = int(time.time())

    mode = calc_market_mode_for_symbol(symbol, now_ts=now_ts)
    raw_path = _symbol_raw_path(symbol)

    data: Dict[str, Any]
    try:
        with raw_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

    tf1 = str(data.get("tf1")) if data.get("tf1") is not None else ""
    tf2 = str(data.get("tf2")) if data.get("tf2") is not None else ""

    state: Dict[str, Any] = {
        "symbol": symbol,
        "tf1": tf1,
        "tf2": tf2,
        "updated_ts": now_ts,
        "market_mode": mode,
        "MA30": 0.0,
        "ATR14": 0.0,
        "trading_params": {},
    }

    # MA30 и ATR14 берём из блока raw[tf1]["signal"], если есть
    raw_block = {}
    raw_all = data.get("raw")
    if isinstance(raw_all, dict) and tf1 and tf1 in raw_all:
        raw_block = raw_all.get(tf1) or {}

    signal = {}
    if isinstance(raw_block, dict):
        signal = raw_block.get("signal") or {}

    state["MA30"] = _safe_float(signal.get("ma30"), 0.0)
    state["ATR14"] = _safe_float(signal.get("atr14"), 0.0)

    trading_params_src = data.get("trading_params") or {}
    if isinstance(trading_params_src, dict):
        state["trading_params"] = _normalize_trading_params_for_state(trading_params_src)
    else:
        state["trading_params"] = {}

    # Сохраняем state в файл
    spath = _state_path(symbol)
    spath.parent.mkdir(parents=True, exist_ok=True)
    try:
        with spath.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        # Даже если не удалось сохранить, всё равно возвращаем вычисленный словарь
        pass

    return state
