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
    """SMA14 + ATR14"""
    if not klines:
        return {"sma14": None, "atr14": None}

    ohlcv = extract_ohlcv(klines)

    return {
        "sma14": calculate_sma(ohlcv["closes"], 14),
        "atr14": calculate_atr(ohlcv["highs"], ohlcv["lows"], ohlcv["closes"], 14),
    }
