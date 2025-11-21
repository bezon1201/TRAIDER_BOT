import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import URLError, HTTPError

from config import STORAGE_DIR, HTTP_PROXY, HTTPS_PROXY, TF1, TF2

log = logging.getLogger(__name__)

BINANCE_BASE_URL = "https://api.binance.com"


def _build_opener() -> Any:
    """Создаём opener с учётом HTTP/HTTPS прокси из конфига (если заданы)."""
    proxies: Dict[str, str] = {}
    if HTTP_PROXY:
        proxies["http"] = HTTP_PROXY
    if HTTPS_PROXY:
        proxies["https"] = HTTPS_PROXY

    if proxies:
        handler = ProxyHandler(proxies)
        opener = build_opener(handler)
    else:
        opener = build_opener()

    return opener


_OPENER = _build_opener()


def _binance_get(path: str, params: Dict[str, Any]) -> Any:
    """Простейший GET-запрос к публичным REST-эндпоинтам Binance."""
    query = urlencode(params)
    url = f"{BINANCE_BASE_URL}{path}?{query}"

    req = Request(url, method="GET")
    req.add_header("Accept", "application/json")

    try:
        with _OPENER.open(req, timeout=60) as resp:
            data = resp.read().decode("utf-8")
    except HTTPError as e:
        log.error("HTTPError от Binance: %s %s", e.code, e.reason)
        raise
    except URLError as e:
        log.error("URLError при запросе к Binance: %s", e.reason)
        raise
    except TimeoutError as e:  # type: ignore[unreachable]
        # Отдельно логируем таймауты Binance без полного traceback, чтобы не засорять консоль
        log.warning("Timeout при запросе к Binance: %s", e)
        raise
    except Exception as e:  # noqa: BLE001
        # Прочие ошибки Binance тоже логируем без traceback
        log.error("Неизвестная ошибка при запросе к Binance: %s", e)
        raise

    try:
        return json.loads(data)
    except json.JSONDecodeError:
        log.error("Не удалось распарсить JSON от Binance: %s", data[:200])
        raise


def fetch_klines(symbol: str, interval: str, limit: int = 200) -> List[List[Any]]:
    """Забирает сырые свечи с Binance для symbol/interval."""
    if limit <= 0:
        raise ValueError("limit должен быть > 0")

    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit,
    }
    data = _binance_get("/api/v3/klines", params)

    # Binance на ошибку возвращает dict с кодом/сообщением
    if isinstance(data, dict) and "code" in data:
        log.error("Ошибка Binance при запросе klines для %s: %s", symbol, data)
        raise RuntimeError(f"Binance error: {data}")

    return data  # список списков


def klines_to_candles(klines: List[List[Any]]) -> List[Dict[str, Any]]:
    """Преобразует kline-данные Binance в список свечей понятной структуры."""
    candles: List[Dict[str, Any]] = []

    for row in klines:
        try:
            open_time = int(row[0]) // 1000  # Binance даёт миллисекунды
            o = float(row[1])
            h = float(row[2])
            l = float(row[3])
            c = float(row[4])
            v = float(row[5])
        except (IndexError, ValueError, TypeError) as e:
            log.error("Некорректная строка kline: %s (%s)", row, e)
            continue

        candles.append(
            {
                "ts": open_time,
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v,
            }
        )

    return candles


def sma(values: List[float], period: int) -> List[Optional[float]]:
    """Простая скользящая средняя (SMA)."""
    if period <= 0:
        raise ValueError("period для SMA должен быть > 0")

    n = len(values)
    if n == 0:
        return []

    if n < period:
        # Недостаточно точек для хотя бы одного значения
        return [None] * n

    result: List[Optional[float]] = [None] * n
    window_sum = sum(values[0:period])
    result[period - 1] = window_sum / period

    for i in range(period, n):
        window_sum += values[i] - values[i - period]
        result[i] = window_sum / period

    return result


def atr14(candles: List[Dict[str, Any]], period: int = 14) -> List[Optional[float]]:
    """Average True Range (ATR) с периодом по умолчанию 14."""
    if period <= 0:
        raise ValueError("period для ATR должен быть > 0")

    n = len(candles)
    if n == 0:
        return []

    true_ranges: List[Optional[float]] = [None] * n
    prev_close = float(candles[0]["c"])

    for i in range(1, n):
        high = float(candles[i]["h"])
        low = float(candles[i]["l"])
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges[i] = tr
        prev_close = float(candles[i]["c"])

    # первая точка TR определена просто как high - low
    first_high = float(candles[0]["h"])
    first_low = float(candles[0]["l"])
    true_ranges[0] = first_high - first_low

    # Преобразуем None → 0.0 на всякий случай
    tr_values = [float(tr) if tr is not None else 0.0 for tr in true_ranges]
    return sma(tr_values, period)


def make_signal(
    ma30_arr: List[Optional[float]],
    ma90_arr: List[Optional[float]],
    atr_arr: List[Optional[float]],
) -> Dict[str, Optional[float] | str]:
    """Формирует сигнал UP/DOWN/RANGE на основе MA30/MA90 и их динамики."""
    n = len(ma30_arr)
    if len(ma90_arr) != n:
        raise ValueError("ma30_arr и ma90_arr должны быть одинаковой длины")

    # Находим последнюю точку, где MA не None
    last_idx: Optional[int] = None
    for i in range(n - 1, -1, -1):
        if ma30_arr[i] is not None and ma90_arr[i] is not None:
            last_idx = i
            break

    if last_idx is None:
        return {
            "value": "RANGE",
            "ma30": None,
            "ma90": None,
            "atr14": None,
            "d_now": None,
            "d_prev": None,
        }

    # Ищем предыдущую валидную точку
    prev_idx: Optional[int] = None
    for j in range(last_idx - 1, -1, -1):
        if ma30_arr[j] is not None and ma90_arr[j] is not None:
            prev_idx = j
            break

    ma30 = float(ma30_arr[last_idx])  # type: ignore[arg-type]
    ma90 = float(ma90_arr[last_idx])  # type: ignore[arg-type]

    atr_val: Optional[float] = None
    if 0 <= last_idx < len(atr_arr) and atr_arr[last_idx] is not None:
        atr_val = float(atr_arr[last_idx])  # type: ignore[arg-type]

    d_now = ma30 - ma90

    if prev_idx is not None:
        ma30_prev = float(ma30_arr[prev_idx])  # type: ignore[arg-type]
        ma90_prev = float(ma90_arr[prev_idx])  # type: ignore[arg-type]
        d_prev = ma30_prev - ma90_prev
    else:
        d_prev = 0.0

    if d_now > 0 and d_prev >= 0:
        value: str = "UP"
    elif d_now < 0 and d_prev <= 0:
        value = "DOWN"
    else:
        value = "RANGE"

    return {
        "value": value,
        "ma30": ma30,
        "ma90": ma90,
        "atr14": atr_val,
        "d_now": d_now,
        "d_prev": d_prev,
    }


def collect_tf_block(
    symbol: str,
    interval: str,
    limit: int = 100,
    ma_short: int = 30,
    ma_long: int = 90,
    atr_period: int = 14,
) -> Dict[str, Any]:
    """Сбор метрик для одной монеты и одного таймфрейма."""
    raw_klines = fetch_klines(symbol, interval, limit=limit)
    candles = klines_to_candles(raw_klines)

    # ограничиваем до 100 последних свечей
    if len(candles) > 100:
        candles = candles[-100:]

    closes = [c["c"] for c in candles]
    highs = [c["h"] for c in candles]
    lows = [c["l"] for c in candles]

    ma_short_arr = sma(closes, ma_short)
    ma_long_arr = sma(closes, ma_long)
    atr_arr = atr14(candles, period=atr_period)

    return {
        "candles": candles,
        "ma_short_period": ma_short,
        "ma_long_period": ma_long,
        "atr_period": atr_period,
        "ma_short_arr": ma_short_arr,
        "ma_long_arr": ma_long_arr,
        "atr_arr": atr_arr,
    }


def fetch_trading_params(symbol: str) -> Dict[str, Any]:
    """Получает торговые параметры символа: цены, фильтры и комиссии."""
    symbol_u = symbol.upper()

    price_info: Dict[str, float] = {}
    symbol_info: Dict[str, Any] = {}
    filters: Dict[str, Any] = {}

    # Last price
    try:
        ticker = _binance_get("/api/v3/ticker/price", {"symbol": symbol_u})
        if isinstance(ticker, dict) and "price" in ticker:
            price_info["last"] = float(ticker["price"])
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось получить ticker/price для %s: %s", symbol_u, e)

    # Bid/Ask
    try:
        book = _binance_get("/api/v3/ticker/bookTicker", {"symbol": symbol_u})
        if isinstance(book, dict):
            bid = book.get("bidPrice")
            ask = book.get("askPrice")
            if bid is not None:
                try:
                    price_info["bid"] = float(bid)
                except ValueError:
                    pass
            if ask is not None:
                try:
                    price_info["ask"] = float(ask)
                except ValueError:
                    pass
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось получить bookTicker для %s: %s", symbol_u, e)

    # exchangeInfo
    try:
        ex = _binance_get("/api/v3/exchangeInfo", {"symbol": symbol_u})
        if isinstance(ex, dict):
            symbols = ex.get("symbols") or []
            if symbols:
                s = symbols[0]
                symbol_info["base_asset"] = s.get("baseAsset")
                symbol_info["quote_asset"] = s.get("quoteAsset")

                f_list = s.get("filters") or []
                filt_map: Dict[str, Any] = {}
                for f in f_list:
                    f_type = f.get("filterType")
                    if f_type:
                        filt_map[f_type] = f
                filters = filt_map

                price_f = filt_map.get("PRICE_FILTER")
                lot_f = filt_map.get("LOT_SIZE")
                notional_f = filt_map.get("MIN_NOTIONAL") or filt_map.get("NOTIONAL")

                if price_f and "tickSize" in price_f:
                    try:
                        symbol_info["tick_size"] = float(price_f["tickSize"])
                    except (TypeError, ValueError):
                        pass

                if lot_f:
                    step_size = lot_f.get("stepSize")
                    min_qty = lot_f.get("minQty")
                    if step_size is not None:
                        try:
                            symbol_info["step_size"] = float(step_size)
                        except (TypeError, ValueError):
                            pass
                    if min_qty is not None:
                        try:
                            symbol_info["min_qty"] = float(min_qty)
                        except (TypeError, ValueError):
                            pass

                if notional_f and "minNotional" in notional_f:
                    try:
                        symbol_info["min_notional"] = float(notional_f["minNotional"])
                    except (TypeError, ValueError):
                        pass
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось получить exchangeInfo для %s: %s", symbol_u, e)

    # Фиксированные комиссии (можно будет вынести в конфиг, если понадобится)
    fees = {
        "maker": 0.001,
        "taker": 0.001,
    }

    params: Dict[str, Any] = {}
    if price_info:
        params["price"] = price_info
    if symbol_info:
        params["symbol_info"] = symbol_info
    if filters:
        params["filters"] = filters
    params["fees"] = fees

    return params


def append_raw_market_line(symbol: str, data: Dict[str, Any]) -> None:
    """Добавляет строку в <COIN>raw_market.jsonl с режимом рынка и сигналами."""
    symbol_u = symbol.upper()
    storage_path = Path(STORAGE_DIR)
    storage_path.mkdir(parents=True, exist_ok=True)
    path = storage_path / f"{symbol_u}raw_market.jsonl"

    tf1 = data.get("tf1", TF1)
    tf2 = data.get("tf2", TF2)
    raw = data.get("raw", {})

    block1 = raw.get(tf1, {}) or {}
    block2 = raw.get(tf2, {}) or {}

    sig1 = (block1.get("signal") or {}).get("value")
    sig2 = (block2.get("signal") or {}).get("value")

    line = {
        "ts": data.get("updated_ts", int(time.time())),
        "symbol": symbol_u,
        "market_mode": data.get("market_mode"),
        "tf1": tf1,
        "tf2": tf2,
        "signal_tf1": sig1,
        "signal_tf2": sig2,
    }

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def update_coin_json(symbol: str) -> Dict[str, Any]:
    """Обновляет (или создаёт) файл <COIN>.json в STORAGE_DIR."""
    symbol_u = symbol.upper()
    tf1 = TF1
    tf2 = TF2
    now_ts = int(time.time())

    storage_path = Path(STORAGE_DIR)
    storage_path.mkdir(parents=True, exist_ok=True)
    path = storage_path / f"{symbol_u}.json"

    # Пытаемся читать существующие данные (чтобы не терять лишние поля)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось прочитать %s, перезаписываем: %s", path, e)
            data = {}
    else:
        data = {}

    data.setdefault("symbol", symbol_u)
    data["tf1"] = tf1
    data["tf2"] = tf2
    data["updated_ts"] = now_ts

    raw = data.setdefault("raw", {})
    block1 = raw.setdefault(tf1, {})
    block2 = raw.setdefault(tf2, {})

    # Собираем метрики по каждому ТФ
    block1_metrics = collect_tf_block(symbol_u, tf1)
    block2_metrics = collect_tf_block(symbol_u, tf2)

    # Обновляем блоки TF1/TF2
    for block, metrics in ((block1, block1_metrics), (block2, block2_metrics)):
        block["candles"] = metrics["candles"]
        block["ma30_arr"] = metrics["ma_short_arr"]
        block["ma90_arr"] = metrics["ma_long_arr"]
        block["atr14_arr"] = metrics["atr_arr"]

        signal = make_signal(
            block["ma30_arr"],
            block["ma90_arr"],
            block["atr14_arr"],
        )
        block["signal"] = signal

    # Общий режим рынка на основе сигналов двух таймфреймов
    sig1 = block1.get("signal", {}).get("value")
    sig2 = block2.get("signal", {}).get("value")

    market_mode = "RANGE"
    if sig1 == "UP" and sig2 == "UP":
        market_mode = "UP"
    elif sig1 == "DOWN" or sig2 == "DOWN":
        market_mode = "DOWN"

    data["market_mode"] = market_mode

    # Торговые параметры символа
    try:
        trading_params = fetch_trading_params(symbol_u)
        if trading_params:
            data["trading_params"] = trading_params
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось обновить trading_params для %s: %s", symbol_u, e)

    # Сохраняем json
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Лог рынка
    try:
        append_raw_market_line(symbol_u, data)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось записать raw_market лог для %s: %s", symbol_u, e)

    log.info("Обновлён файл метрик для %s: %s", symbol_u, path)
    return data




def get_symbol_last_price_light(symbol: str) -> Optional[float]:
    """Лёгкий запрос к Binance для получения только last-цены по символу.

    Используется для технических операций (например, REFRESH в ORDERS),
    чтобы не запускать тяжёлый сбор всех метрик.
    """
    symbol_u = (symbol or "").strip().upper()
    if not symbol_u:
        return None

    try:
        data = _binance_get("/api/v3/ticker/price", {"symbol": symbol_u})
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось получить лёгкий ticker/price для %s: %s", symbol_u, e)
        return None

    if isinstance(data, dict):
        price = data.get("price")
        try:
            return float(price)
        except (TypeError, ValueError):
            return None

    # На всякий случай обрабатываем варианты с массивом
    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            price = item.get("price")
            try:
                return float(price)
            except (TypeError, ValueError):
                return None

    return None


def update_metrics_for_coins(coins: List[str]) -> None:
    """Обновляет метрики для всех монет из списка."""
    for symbol in coins:
        try:
            update_coin_json(symbol)
        except Exception as e:  # noqa: BLE001
            # Логируем коротко без traceback — детали уже есть выше по стеку
            log.error("Ошибка при обновлении метрик для %s: %s", symbol, e)
