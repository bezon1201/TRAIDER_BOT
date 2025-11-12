import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

def calculate_sma(values: List[float], period: int) -> Optional[float]:
    """SMA расчет"""
    if not values or len(values) < period:
        return None
    return sum(values[-period:]) / period

def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    """ATR расчет"""
    if not highs or not lows or not closes or len(closes) < period:
        return None

    tr_values = []
    for i in range(len(closes)):
        if i == 0:
            tr = highs[i] - lows[i]
        else:
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
        tr_values.append(tr)

    return sum(tr_values[-period:]) / period

def extract_ohlcv(klines: List[List[Any]]) -> Dict[str, List[float]]:
    """OHLCV из klines"""
    if not klines:
        return {"opens": [], "highs": [], "lows": [], "closes": [], "volumes": []}

    opens, highs, lows, closes, volumes = [], [], [], [], []

    for kline in klines:
        try:
            opens.append(float(kline[1]))
            highs.append(float(kline[2]))
            lows.append(float(kline[3]))
            closes.append(float(kline[4]))
            volumes.append(float(kline[7]))
        except (IndexError, ValueError):
            continue

    return {"opens": opens, "highs": highs, "lows": lows, "closes": closes, "volumes": volumes}

def calculate_indicators(klines: List[List[Any]]) -> Dict[str, Any]:
    """SMA14 + ATR14 с историей для расчета raw режимов"""
    if not klines or len(klines) < 14:
        return {"sma14": None, "sma14_prev": None, "atr14": None, "atr14_prev": None}

    ohlcv = extract_ohlcv(klines)
    closes = ohlcv["closes"]
    highs = ohlcv["highs"]
    lows = ohlcv["lows"]

    # Текущие значения
    sma14_now = calculate_sma(closes, 14)
    atr14_now = calculate_atr(highs, lows, closes, 14)

    # Предыдущие значения (если есть минимум 28 свечей)
    sma14_prev = None
    atr14_prev = None

    if len(closes) >= 28:
        sma14_prev = calculate_sma(closes[:-1], 14)
        atr14_prev = calculate_atr(highs[:-1], lows[:-1], closes[:-1], 14)

    return {
        "sma14": sma14_now,
        "sma14_prev": sma14_prev,
        "atr14": atr14_now,
        "atr14_prev": atr14_prev
    }
