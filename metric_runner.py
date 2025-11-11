
import json, random
from pathlib import Path
from datetime import datetime, timezone, timedelta

async def run_now_for_symbol(symbol: str, storage_dir: str):
    d = Path(storage_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{symbol}.json"
    now = datetime.now(timezone.utc)
    base = {
        "symbol": symbol,
        "tf": {},
        "updated_utc": now.isoformat()
    }
    # toy metrics for 6h/12h
    for tf in ("6h", "12h"):
        sma30 = 100 + random.uniform(-1, 1)
        sma90 = 100 + random.uniform(-1, 1)
        block = {
            "bar_time_utc": now.isoformat(),
            "close_last": 100 + random.uniform(-3, 3),
            "ATR14": abs(random.uniform(0.3, 2.0)),
            "SMA30": sma30,
            "SMA90": sma90,
            "SMA30_arr": [sma30 - 0.1, sma30],
            "SMA90_arr": [sma90 - 0.05, sma90],
        }
        base["tf"][tf] = block
    p.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    return base
