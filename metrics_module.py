from aiogram import Router
router = Router()

def collect_metrics_for_symbol(symbol: str, storage_dir: str) -> dict:
    # stub metric collection; keep interface stable
    return {"symbol": symbol, "updated_utc": None}

def read_coins_list(storage_dir: str) -> list[str]:
    from pathlib import Path
    p = Path(storage_dir) / "coins.txt"
    if not p.exists():
        return []
    raw = p.read_text(encoding="utf-8")
    items = [x.strip().lower() for x in raw.split(",") if x.strip()]
    # also allow newline-separated
    more = [x.strip().lower() for x in raw.splitlines() if x.strip()]
    s = set(items) | set(more)
    return sorted(s)
