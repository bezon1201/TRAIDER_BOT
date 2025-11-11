import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

def calculate_sma(values: List[float], period: int) -> Optional[float]:
    """Расчет простой скользящей средней (SMA)"""
    if not values or len(values) < period:
        return None

    return sum(values[-period:]) / period

def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    """Расчет среднего истинного диапазона (ATR) для периода 14"""
    if not highs or not lows or not closes:
        return None

    if len(highs) < period or len(lows) < period or len(closes) < period:
        return None

    # Вычисляем True Range для каждого периода
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

    # ATR = SMA от True Range за последние 14 периодов
    atr = sum(tr_values[-period:]) / period

    return atr

def extract_ohlcv_from_klines(klines: List[List[Any]]) -> Dict[str, List[float]]:
    """Извлекает OHLCV данные из свечей Binance"""
    if not klines:
        return {"opens": [], "highs": [], "lows": [], "closes": [], "volumes": []}

    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []

    for kline in klines:
        # Формат Binance kline:
        # [time, open, high, low, close, volume, ...]
        try:
            opens.append(float(kline[1]))
            highs.append(float(kline[2]))
            lows.append(float(kline[3]))
            closes.append(float(kline[4]))
            volumes.append(float(kline[7]))  # Quote asset volume
        except (IndexError, ValueError) as e:
            logger.warning(f"Error parsing kline: {e}")
            continue

    return {
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": volumes,
    }

def calculate_indicators(klines: List[List[Any]]) -> Dict[str, Any]:
    """Рассчитывает SMA14 и ATR14 для свечей"""
    if not klines:
        return {"sma14": None, "atr14": None}

    ohlcv = extract_ohlcv_from_klines(klines)

    closes = ohlcv["closes"]
    highs = ohlcv["highs"]
    lows = ohlcv["lows"]

    sma14 = calculate_sma(closes, 14)
    atr14 = calculate_atr(highs, lows, closes, 14)

    return {
        "sma14": sma14,
        "atr14": atr14,
    }
