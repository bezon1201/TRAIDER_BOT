import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

TIMEFRAMES_LONG = ["12h", "6h"]
TIMEFRAMES_SHORT = ["4h", "2h"]

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

def calculate_raw_signal(metrics: Dict[str, Any], mode: str) -> Optional[Dict[str, Any]]:
    try:
        if mode == "LONG":
            tfs = TIMEFRAMES_LONG
        elif mode == "SHORT":
            tfs = TIMEFRAMES_SHORT
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
        return {"timestamp": datetime.now(timezone.utc).isoformat(), "signal": overall_signal, "signals": signals, "frame": mode}
    except Exception as e:
        logger.error(f"Error calculating raw signal for {frame}: {e}")
        return None

def append_raw_market(storage_dir: str, symbol: str, mode: str, raw_data: Dict[str, Any]) -> bool:
    try:
        filename = f"{symbol}_raw_market_{mode}.jsonl"
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
        logger.error(f"Error appending raw market {symbol} {mode}: {e}")
        try:
            tmp_filepath.unlink()
        except:
            pass
        return False

def calculate_and_save_raw_markets(storage_dir: str, symbol: str, metrics: Dict[str, Any]) -> bool:
    try:
        raw_long = calculate_raw_signal(metrics, "LONG")
        if raw_long:
            append_raw_market(storage_dir, symbol, "LONG", raw_long)
        raw_short = calculate_raw_signal(metrics, "SHORT")
        if raw_short:
            append_raw_market(storage_dir, symbol, "SHORT", raw_short)hort)
        logger.info(f"✓ Raw markets calculated for {symbol}")
        return True
    except Exception as e:
        logger.error(f"Error calculating raw markets for {symbol}: {e}")
        return False

def force_market_mode(storage_dir: str, symbol: str, mode: str) -> str:
    if mode not in ["LONG", "SHORT"]:
        return "Неподдерживаемый фрейм"

    raw_file = Path(storage_dir) / f"{symbol}_raw_market_{mode}.jsonl"
    if not raw_file.exists():
        return f"Файл не найден"

    now = datetime.now(timezone.utc)
    signals_count = {"UP": 0, "DOWN": 0, "RANGE": 0}
    one_day_ago = now - timedelta(days=1)

    lines = []
    with open(raw_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    recent_records = []
    for line in lines:
        try:
            record = json.loads(line)
            ts = datetime.fromisoformat(record["timestamp"])
            if ts >= one_day_ago:
                recent_records.append(record)
        except:
            continue

    if len(recent_records) < 10:
        recent_records = []
        for line in lines:
            try:
                record = json.loads(line)
                recent_records.append(record)
            except:
                continue

    for rec in recent_records:
        sig = rec.get("signal", "RANGE")
        signals_count[sig] = signals_count.get(sig, 0) + 1

    total = len(recent_records)
    if total == 0:
        return "Нет данных"

    market_mode = "RANGE"
    for mode, count in signals_count.items():
        if count / total > 0.6:
            market_mode = mode
            break

    json_file = Path(storage_dir) / f"{symbol}.json"
    data = {}
    if json_file.exists():
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            data = {}
    data["market_mode"] = market_mode
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    from metrics import set_market_mode
    set_market_mode(storage_dir, symbol, market_mode)
    return market_mode
