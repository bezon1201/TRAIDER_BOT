# -*- coding: utf-8 -*-
"""
market_calculation.py — keeps both 12+6 and 6+4 paths; adds bias-based helper.
"""
import os, json, time, logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

TIMEFRAMES_12_6 = ["12h", "6h"]
TIMEFRAMES_6_4 = ["6h", "4h"]

def calculate_signal(tf_data: Dict[str, Any]) -> str:
    """
    Very simplified example: derive signal from indicators.
    Real logic should reflect your existing SMA/ATR rules.
    """
    ind = tf_data.get("indicators", {})
    sma = ind.get("sma14", ind.get("sma30", 0))
    atr = ind.get("atr14", 0)
    last_close = tf_data.get("klines", [{}])[-1].get("close", 0) if tf_data.get("klines") else 0
    if last_close > sma:
        return "UP"
    if last_close < sma:
        return "DOWN"
    return "RANGE"

def calculate_raw_signal(metrics: Dict[str, Any], frame: str) -> Optional[Dict[str, Any]]:
    if frame == "12+6":
        tfs = TIMEFRAMES_12_6
    elif frame == "6+4":
        tfs = TIMEFRAMES_6_4
    else:
        return None
    signals = {}
    for tf in tfs:
        tf_data = metrics.get("timeframes", {}).get(tf, {})
        signals[tf] = calculate_signal(tf_data)
    combined = "RANGE"
    if all(s == "UP" for s in signals.values()):
        combined = "UP"
    elif all(s == "DOWN" for s in signals.values()):
        combined = "DOWN"
    return {
        "ts": int(time.time() * 1000),
        "frame": frame,
        "signals": signals,
        "combined": combined,
    }

def append_raw_market(storage_dir: str, symbol: str, frame: str, raw_data: Dict[str, Any]) -> bool:
    try:
        os.makedirs(storage_dir, exist_ok=True)
        filename = os.path.join(storage_dir, f"{symbol}_raw_market_{frame}.jsonl")
        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(raw_data, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        logger.error(f"append_raw_market error for {symbol} {frame}: {e}")
        return False

def _load_symbol_metrics(storage_dir: str, symbol: str) -> Dict[str, Any]:
    path = os.path.join(storage_dir, f"{symbol}.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def calculate_and_save_raw_markets(storage_dir: str, symbol: str, frame: Optional[str] = None) -> bool:
    """
    If frame is None -> process both frames (backward compat).
    If frame is "12+6" or "6+4" -> process only that one.
    """
    try:
        metrics = _load_symbol_metrics(storage_dir, symbol)
        frames: List[str]
        if frame in ("12+6", "6+4"):
            frames = [frame]
        else:
            frames = ["12+6", "6+4"]
        for fr in frames:
            raw = calculate_raw_signal(metrics, fr)
            if raw:
                append_raw_market(storage_dir, symbol, fr, raw)
        logger.info(f"✓ Raw markets calculated for {symbol} ({','.join(frames)})")
        return True
    except Exception as e:
        logger.error(f"Error calculating raw markets for {symbol}: {e}")
        return False

def _read_recent_raw(storage_dir: str, symbol: str, frame: str, min_count: int = 10, lookback_seconds: int = 86400) -> List[Dict[str, Any]]:
    path = os.path.join(storage_dir, f"{symbol}_raw_market_{frame}.jsonl")
    arr: List[Dict[str, Any]] = []
    if not os.path.exists(path):
        return arr
    now_ms = int(time.time() * 1000)
    lb_ms = now_ms - lookback_seconds * 1000
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                j = json.loads(line)
                if j.get("ts", 0) >= lb_ms:
                    arr.append(j)
            except Exception:
                continue
    if len(arr) < min_count:
        # fallback to all
        arr = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    arr.append(json.loads(line))
                except Exception:
                    pass
    return arr[-1000:]  # cap

def force_market_mode(storage_dir: str, symbol: str, frame: str) -> str:
    if frame not in ("12+6", "6+4"):
        return "RANGE"
    raws = _read_recent_raw(storage_dir, symbol, frame)
    if not raws:
        mode = "RANGE"
    else:
        last = raws[-200:]  # window
        ups = sum(1 for r in last if r.get("combined") == "UP")
        downs = sum(1 for r in last if r.get("combined") == "DOWN")
        mode = "RANGE"
        if ups > downs and ups >= len(last)*0.55:
            mode = "UP"
        elif downs > ups and downs >= len(last)*0.55:
            mode = "DOWN"
    # write to symbol json
    sym_path = os.path.join(storage_dir, f"{symbol}.json")
    data = {}
    if os.path.exists(sym_path):
        with open(sym_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    data["market_mode"] = mode
    with open(sym_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"{symbol} market_mode={mode} via {frame}")
    return mode

def run_market_pipeline_by_bias(storage_dir: str, symbol: str, bias: str) -> str:
    frame = "12+6" if bias == "LONG" else "6+4"
    calculate_and_save_raw_markets(storage_dir, symbol, frame=frame)
    return force_market_mode(storage_dir, symbol, frame=frame)
