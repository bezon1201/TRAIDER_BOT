
import os, json, time
from typing import Dict, Tuple, List
from datetime import datetime, timezone

H_K = 0.6
S_K = 0.1
RAW_MAX_BYTES = 10 * 1024 * 1024  # 10 MB cap

def _now_ts() -> int:
    return int(time.time())

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

def _tf_signal(tf_block: Dict) -> str:
    if not tf_block:
        return "RANGE"
    ma30 = float(tf_block.get("MA30", 0.0) or 0.0)
    ma90 = float(tf_block.get("MA90", 0.0) or 0.0)
    atr  = float(tf_block.get("ATR14", 0.0) or 0.0)
    ma30_arr = tf_block.get("MA30_arr") or []
    ma90_arr = tf_block.get("MA90_arr") or []
    d_now = ma30 - ma90
    d_prev = (float(ma30_arr[-1]) - float(ma90_arr[-1])) if (ma30_arr and ma90_arr) else d_now
    if atr <= 0:
        return "RANGE"
    H = H_K * atr
    S = S_K * atr
    if d_now > +H and (d_now - d_prev) >= +S:
        return "UP"
    if d_now < -H and (d_now - d_prev) <= -S:
        return "DOWN"
    return "RANGE"

def compute_overall_mode_from_metrics(metrics: Dict) -> Tuple[str, Dict[str,str]]:
    tf = metrics.get("tf") or {}
    sig6  = _tf_signal(tf.get("6h"))
    sig12 = _tf_signal(tf.get("12h"))
    if sig12 == "UP" and sig6 == "UP":
        overall = "UP"
    elif (sig12 == "DOWN") or (sig6 == "DOWN"):
        overall = "DOWN"
    else:
        overall = "RANGE"
    return overall, {"12h": sig12, "6h": sig6}

def _raw_log_path(storage_dir: str, symbol_lc: str) -> str:
    return os.path.join(storage_dir, f"mode_raw_{symbol_lc}.jsonl")

def _enforce_size_limit(path: str, max_bytes: int = RAW_MAX_BYTES) -> int:
    """If file exceeds max_bytes, trim oldest lines until under cap.
       Returns resulting file size in bytes (or 0 if file missing)."""
    if not os.path.exists(path):
        return 0
    try:
        sz = os.path.getsize(path)
        if sz <= max_bytes:
            return sz
        # Trim by reading and keeping the newest lines that fit into cap (with a buffer)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # Keep last N lines until size <= cap
        kept = []
        total = 0
        for line in reversed(lines):
            bl = len(line.encode("utf-8", "ignore"))
            if total + bl > max_bytes:
                break
            kept.append(line)
            total += bl
        kept.reverse()
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)
        return os.path.getsize(path)
    except Exception:
        return os.path.getsize(path) if os.path.exists(path) else 0

def append_raw_snapshot(storage_dir: str, symbol_lc: str, overall_raw: str, tf_signals: Dict[str,str]) -> None:
    os.makedirs(storage_dir, exist_ok=True)
    path = _raw_log_path(storage_dir, symbol_lc)
    rec = {"ts": _now_ts(), "overall_raw": overall_raw, "tf": tf_signals}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\\n")
    except Exception:
        return
    # enforce cap after append
    _enforce_size_limit(path, RAW_MAX_BYTES)

def _read_recent_raw(storage_dir: str, symbol_lc: str, hours: int) -> List[Dict]:
    path = _raw_log_path(storage_dir, symbol_lc)
    if not os.path.exists(path):
        return []
    since = _now_ts() - hours * 3600
    out: List[Dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                if rec.get("ts", 0) >= since:
                    out.append(rec)
            except Exception:
                continue
    return out

def _majority_mode(records: List[Dict]) -> str:
    if not records:
        return "RANGE"
    cnt = {"UP":0, "RANGE":0, "DOWN":0}
    for r in records:
        m = r.get("overall_raw", "RANGE")
        if m in cnt:
            cnt[m] += 1
    total = sum(cnt.values()) or 1
    for key in ("UP","DOWN"):
        if cnt[key] / total > 0.6:
            return key
    return "RANGE"

def publish_if_due(storage_dir: str, symbol_lc: str, cfg: Dict) -> None:
    now_ts = int(time.time())
    publish_hours = int(cfg.get("publish_hours", 24))
    next_pub = cfg.get("next_publish_utc")
    if next_pub is None or now_ts < next_pub:
        return
    _publish(storage_dir, symbol_lc, publish_hours)
    cfg["last_publish_utc"] = now_ts
    cfg["next_publish_utc"] = now_ts + publish_hours * 3600

def _publish(storage_dir: str, symbol_lc: str, publish_hours: int) -> None:
    recs = _read_recent_raw(storage_dir, symbol_lc, publish_hours)
    final_mode = _majority_mode(recs)
    path = os.path.join(storage_dir, f"{s_symbol(symbol_lc)}.json")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
        last_tf = recs[-1]["tf"] if recs else {"12h":"RANGE","6h":"RANGE"}
        data["market_mode"] = final_mode or "RANGE"
        data["signals"] = last_tf
        data["mode_updated_utc"] = _now_iso()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass

def s_symbol(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

# --- helpers for /market ---
def compute_mode_from_raw(storage_dir: str, symbol_lc: str, hours: int) -> Tuple[str | None, Dict | None]:
    recs = _read_recent_raw(storage_dir, symbol_lc, hours)
    if not recs:
        return None, None
    return _majority_mode(recs), (recs[-1].get("tf") if recs else None)

def force_publish_now(storage_dir: str, symbol_lc: str, cfg: Dict) -> None:
    hours = int(cfg.get("publish_hours", 24))
    _publish(storage_dir, symbol_lc, hours)
    now_ts = int(time.time())
    cfg["last_publish_utc"] = now_ts
    cfg["next_publish_utc"] = now_ts + hours * 3600

def trim_raw_for_symbol(storage_dir: str, symbol_lc: str, hours: int, max_bytes: int = RAW_MAX_BYTES) -> Tuple[int, int]:
    """Trim raw file by time window and then enforce max size.
       Returns (trimmed_lines, final_size_bytes)."""
    path = _raw_log_path(storage_dir, symbol_lc)
    if not os.path.exists(path):
        return 0, 0
    since = _now_ts() - hours * 3600
    trimmed = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        kept = []
        for line in lines:
            try:
                rec = json.loads(line)
                if rec.get("ts", 0) >= since:
                    kept.append(line)
                else:
                    trimmed += 1
            except Exception:
                # drop broken lines
                trimmed += 1
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(kept)
    except Exception:
        pass
    final_size = _enforce_size_limit(path, max_bytes)
    return trimmed, final_size
