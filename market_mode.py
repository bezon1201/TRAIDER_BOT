import json
from pathlib import Path
from typing import Dict, Tuple

TF_KEYS = ["6h", "12h"]

def _load_metrics(storage_dir: str, symbol: str) -> dict | None:
    p = Path(storage_dir) / f"{symbol}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def _signal_for_tf(tf_block: dict) -> str:
    if not isinstance(tf_block, dict):
        return "RANGE"
    close = tf_block.get("close_last", 0.0) or 0.0
    atr = tf_block.get("ATR14", 0.0) or 0.0
    if (atr or 0.0) <= 0 or (close or 0.0) <= 0:
        return "RANGE"

    ma30 = tf_block.get("SMA30")
    ma90 = tf_block.get("SMA90")
    ma30_arr = tf_block.get("SMA30_arr") or []
    ma90_arr = tf_block.get("SMA90_arr") or []

    diff_now = None
    diff_prev = None
    if isinstance(ma30, (int, float)) and isinstance(ma90, (int, float)):
        diff_now = float(ma30) - float(ma90)
    if len(ma30_arr) >= 2 and len(ma90_arr) >= 2:
        try:
            diff_prev = float(ma30_arr[-2]) - float(ma90_arr[-2])
        except Exception:
            diff_prev = diff_now

    H = 0.6 * float(atr)

    if diff_now is None:
        return "RANGE"
    if diff_prev is None:
        diff_prev = diff_now

    if diff_now > H and diff_now >= diff_prev:
        return "UP"
    if diff_now < -H and diff_now <= diff_prev:
        return "DOWN"
    return "RANGE"

def _overall_from_tf(tf_signals: Dict[str, str]) -> str:
    s6 = tf_signals.get("6h", "RANGE")
    s12 = tf_signals.get("12h", "RANGE")
    if s6 == "UP" and s12 == "UP":
        return "UP"
    if s6 == "DOWN" or s12 == "DOWN":
        return "DOWN"
    return "RANGE"

def evaluate_for_symbol(storage_dir: str, symbol: str) -> Tuple[str, Dict[str, str]]:
    data = _load_metrics(storage_dir, symbol) or {}
    tf = data.get("tf") or {}
    tf_signals = {k: _signal_for_tf(tf.get(k) or {}) for k in TF_KEYS}
    overall = _overall_from_tf(tf_signals)
    return overall, tf_signals

def append_to_log(storage_dir: str, symbol: str, overall: str, tf_signals: Dict[str, str]) -> None:
    d = Path(storage_dir)
    d.mkdir(parents=True, exist_ok=True)
    metrics = _load_metrics(storage_dir, symbol) or {}
    ts = None
    for k in reversed(TF_KEYS):
        blk = (metrics.get("tf") or {}).get(k) or {}
        if "bar_time_utc" in blk:
            ts = blk.get("bar_time_utc")
            break
    rec = {"symbol": symbol, "ts": ts, "overall": overall, "tf": tf_signals}
    with (d / "market.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
