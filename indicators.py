import logging

logger = logging.getLogger(__name__)

def calculate_sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    seed = deltas[:period]
    up = sum([d for d in seed if d > 0]) / period
    down = sum([abs(d) for d in seed if d < 0]) / period
    rs = up / down if down else 0
    return 100 - 100 / (1 + rs)
