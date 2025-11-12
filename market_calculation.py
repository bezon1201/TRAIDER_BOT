import logging
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

TIMEFRAMES_12_6 = ["12h", "6h"]
TIMEFRAMES_4_2 = ["4h", "2h"]

def calculate_signal(tf_data: Dict[str, Any]) -> str:
    try:
        indicators = tf_data.get("indicators", {})
        sma14 = float(indicators.get("sma14") or 0)
        sma14_prev = float(indicators.get("sma14_prev") or 0)
        atr14 = float(indicators.get("atr14") or 0)
        if atr14 <= 0:
            return "RANGE"
        d_now = sma14 - sma14_prev
        d_prev = 0
        H = 0.4 * atr14
        S = 0.1 * atr14
        if d_now > H and (d_now - d_prev) >= S:
            return "UP"
        elif d_now < -H and (d_now - d_prev) <= -S:
            return "DOWN"
        else:
            return "RANGE"
    except Exception as e:
        logger.error(f"Error calculating signal: {e}")
        return "RANGE"

def calculate_raw_signal(metrics: Dict[str, Any], frame: str) -> Optional[Dict[str, Any]]:
    try:
        if frame == "12+6":
            tfs = TIMEFRAMES_12_6
        elif frame == "4+2":
            tfs = TIMEFRAMES_4_2
        else:
            return None
        signals = {}
        for tf in tfs:
            tf_data = metrics.get("timeframes", {}).get(tf, {})
            signals[tf] = calculate_signal(tf_data)
        if signals[tfs[0]] == "UP" and signals[tfs[1]] == "UP":
            overall_signal = "UP"
        elif "DOWN" in signals.values():
            overall_signal = "DOWN"
        else:
            overall_signal = "RANGE"
        return {"timestamp": datetime.now(timezone.utc).isoformat(), "signal": overall_signal, "signals": signals, "frame": frame}
    except Exception as e:
        logger.error(f"Error calculating raw signal for {frame}: {e}")
        return None

def append_raw_market(storage_dir: str, symbol: str, frame: str, raw_data: Dict[str, Any]) -> bool:
    try:
        filename = f"{symbol}_raw_market_{frame}.jsonl"
        filepath = Path(storage_dir) / filename
        tmp_filepath = Path(storage_dir) / (filename + ".tmp")
        existing_lines = []
        if filepath.exists():
            with open(filepath, 'r', encoding='utf-8') as f:
                existing_lines = f.readlines()
        new_line = json.dumps(raw_data, ensure_ascii=False) + '\n'
        with open(tmp_filepath, 'w', encoding='utf-8') as f:
            f.writelines(existing_lines)
            f.write(new_line)
        tmp_filepath.replace(filepath)
        logger.info(f"✓ Raw market saved: {filename}")
        return True
    except Exception as e:
        logger.error(f"Error appending raw market {symbol} {frame}: {e}")
        try:
            tmp_filepath.unlink()
        except:
            pass
        return False

def calculate_and_save_raw_markets(storage_dir: str, symbol: str, metrics: Dict[str, Any]) -> bool:
    try:
        raw_12_6 = calculate_raw_signal(metrics, "12+6")
        if raw_12_6:
            append_raw_market(storage_dir, symbol, "12+6", raw_12_6)
        raw_4_2 = calculate_raw_signal(metrics, "4+2")
        if raw_4_2:
            append_raw_market(storage_dir, symbol, "4+2", raw_4_2)
        logger.info(f"✓ Raw markets calculated for {symbol}")
        return True
    except Exception as e:
        logger.error(f"Error calculating raw markets for {symbol}: {e}")
        return False

def get_raw_market_summary(storage_dir: str, symbol: str, frame: str, hours: int = 72) -> Optional[str]:
    try:
        filename = f"{symbol}_raw_market_{frame}.jsonl"
        filepath = Path(storage_dir) / filename
        if not filepath.exists():
            return None
        cutoff_time = datetime.now(timezone.utc).timestamp() - (hours * 3600)
        up_count = 0
        down_count = 0
        range_count = 0
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    record = json.loads(line)
                    ts = datetime.fromisoformat(record["timestamp"]).timestamp()
                    if ts >= cutoff_time:
                        signal = record.get("signal", "RANGE")
                        if signal == "UP":
                            up_count += 1
                        elif signal == "DOWN":
                            down_count += 1
                        else:
                            range_count += 1
                except:
                    pass
        total = up_count + down_count + range_count
        if total == 0:
            return "RANGE"
        up_pct = (up_count / total) * 100
        down_pct = (down_count / total) * 100
        if up_pct >= 60:
            return "UP"
        elif down_pct >= 60:
            return "DOWN"
        else:
            return "RANGE"
    except Exception as e:
        logger.error(f"Error getting raw market summary {symbol} {frame}: {e}")
        return None
