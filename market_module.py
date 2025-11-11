from aiogram import Router
router = Router()

def append_mode_raw(symbol: str, storage_dir: str, payload: dict) -> None:
    from pathlib import Path
    import json, time
    path = Path(storage_dir) / f"mode_raw_{symbol}.jsonl"
    payload = dict(payload)
    payload.setdefault("symbol", symbol)
    payload.setdefault("ts", int(time.time()))
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
