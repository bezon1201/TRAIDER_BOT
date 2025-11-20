import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import STORAGE_DIR, TF1, TF2, MARKET_PUBLISH

log = logging.getLogger(__name__)

STORAGE_PATH = Path(STORAGE_DIR)


def _raw_market_path(symbol: str) -> Path:
    symbol = (symbol or "").upper()
    return STORAGE_PATH / f"{symbol}raw_market.jsonl"


def _state_path(symbol: str) -> Path:
    symbol = (symbol or "").upper()
    return STORAGE_PATH / f"{symbol}state.json"


def _coin_path(symbol: str) -> Path:
    symbol = (symbol or "").upper()
    return STORAGE_PATH / f"{symbol}.json"


def _load_raw_market_lines(symbol: str, now_ts: Optional[int] = None) -> List[Dict[str, Any]]:
    """Читает лог <COIN>raw_market.jsonl и возвращает записи за окно MARKET_PUBLISH часов."""
    symbol = (symbol or "").upper()
    if not symbol:
        return []

    path = _raw_market_path(symbol)
    if not path.exists():
        return []

    if now_ts is None:
        now_ts = int(time.time())
    window_start = now_ts - MARKET_PUBLISH * 3600

    result: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("Некорректная строка в %s: %r", path, line[:200])
                    continue
                ts_val = obj.get("ts")
                try:
                    ts_int = int(ts_val)
                except (TypeError, ValueError):
                    continue
                if ts_int < window_start:
                    continue
                result.append(obj)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось прочитать лог рынка %s: %s", path, e)

    return result


def calc_market_mode_for_symbol(symbol: str, now_ts: Optional[int] = None) -> str:
    """Считает режим рынка (UP / DOWN / RANGE) по логам <COIN>raw_market.jsonl за последние MARKET_PUBLISH часов."""
    lines = _load_raw_market_lines(symbol, now_ts=now_ts)
    if not lines:
        return "RANGE"

    up = down = rng = 0
    for item in lines:
        mode = (item.get("market_mode") or "").upper()
        if mode == "UP":
            up += 1
        elif mode == "DOWN":
            down += 1
        else:
            rng += 1

    total = up + down + rng
    if total == 0:
        return "RANGE"

    up_ratio = up / total
    down_ratio = down / total

    if up_ratio > 0.5:
        return "UP"
    if down_ratio > 0.5:
        return "DOWN"
    return "RANGE"


def _deep_copy(obj: Any) -> Any:
    """Грубый deepcopy через JSON, чтобы не портить исходные структуры."""
    try:
        return json.loads(json.dumps(obj))
    except Exception:
        return obj


def normalize_trading_params(trading_params: Dict[str, Any]) -> Dict[str, Any]:
    """Нормализует trading_params для state.json.

    - Синхронизирует min_notional с фильтрами NOTIONAL / MIN_NOTIONAL.
    - Добавляет дубли с float-полями (minQty_f, stepSize_f, tickSize_f, minNotional_f и т.п.).
    """
    if not trading_params:
        return {}

    tp = _deep_copy(trading_params)
    symbol_info = tp.get("symbol_info") or {}
    filters = tp.get("filters") or {}

    # Синхронизация min_notional с фильтрами
    notional_f = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL")
    if notional_f is not None:
        min_notional_val = notional_f.get("minNotional")
        try:
            mn_float = float(min_notional_val)
        except (TypeError, ValueError):
            mn_float = None
        if mn_float is not None:
            symbol_info["min_notional"] = mn_float
            notional_f["minNotional_f"] = mn_float

    # Дублируем числовые поля фильтров с *_f
    numeric_keys = {
        "minQty",
        "maxQty",
        "stepSize",
        "tickSize",
        "minNotional",
        "multiplierUp",
        "multiplierDown",
    }

    for f_name, f_obj in list(filters.items()):
        if not isinstance(f_obj, dict):
            continue
        for key, val in list(f_obj.items()):
            if key in numeric_keys:
                try:
                    f_obj[f"{key}_f"] = float(val)
                except (TypeError, ValueError):
                    continue

    tp["symbol_info"] = symbol_info
    tp["filters"] = filters
    return tp


def recalc_state_for_symbol(symbol: str, now_ts: Optional[int] = None) -> Dict[str, Any]:
    """Пересчитывает state для одной монеты и сохраняет в <COIN>state.json."""
    symbol_u = (symbol or "").upper()
    if not symbol_u:
        return {}

    if now_ts is None:
        now_ts = int(time.time())

    cpath = _coin_path(symbol_u)
    if not cpath.exists():
        log.warning("Файл метрик для %s не найден: %s", symbol_u, cpath)
        return {}

    try:
        with cpath.open("r", encoding="utf-8") as f:
            coin: Dict[str, Any] = json.load(f)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось прочитать %s: %s", cpath, e)
        return {}

    tf1 = str(coin.get("tf1", TF1))
    tf2 = str(coin.get("tf2", TF2))

    raw = coin.get("raw") or {}
    block1 = raw.get(tf1) or {}
    signal1 = block1.get("signal") or {}

    ma30 = signal1.get("ma30")
    atr14 = signal1.get("atr14")

    market_mode = calc_market_mode_for_symbol(symbol_u, now_ts=now_ts)

    trading_params_raw = coin.get("trading_params") or {}
    if trading_params_raw:
        trading_params = normalize_trading_params(trading_params_raw)
    else:
        trading_params = {}

    state: Dict[str, Any] = {
        "symbol": symbol_u,
        "tf1": tf1,
        "tf2": tf2,
        "updated_ts": now_ts,
        "market_mode": market_mode,
        "MA30": ma30,
        "ATR14": atr14,
        "trading_params": trading_params,
    }

    spath = _state_path(symbol_u)
    spath.parent.mkdir(parents=True, exist_ok=True)
    try:
        with spath.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось сохранить state для %s в %s: %s", symbol_u, spath, e)

    return state


def recalc_state_for_coins(coins: List[str], now_ts: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    """Пересчитывает state для всех монет из списка."""
    if now_ts is None:
        now_ts = int(time.time())

    result: Dict[str, Dict[str, Any]] = {}
    for symbol in coins:
        try:
            state = recalc_state_for_symbol(symbol, now_ts=now_ts)
            if state:
                result[(symbol or "").upper()] = state
        except Exception as e:  # noqa: BLE001
            log.exception("Ошибка при пересчёте state для %s: %s", symbol, e)

    return result
