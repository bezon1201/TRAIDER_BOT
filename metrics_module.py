
import os, re, asyncio, json, random
from pathlib import Path
from typing import List
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from utils import mono

router = Router()
SAFE = re.compile(r"^[a-z0-9]+$")

def storage_dir() -> Path:
    d = Path(os.getenv("STORAGE_DIR") or "./storage")
    d.mkdir(parents=True, exist_ok=True)
    return d

def coins_file() -> Path:
    return storage_dir() / "coins.txt"

def read_coins() -> List[str]:
    f = coins_file()
    if not f.exists(): return []
    return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]

def parse_csv(raw: str) -> List[str]:
    return [x.strip().lower() for x in (raw.split(",") if raw else []) if x.strip()]

async def run_now_for_symbol(symbol: str, storage: str | None = None):
    d = Path(storage) if storage else storage_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{symbol}.json"
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    base = {"symbol": symbol, "tf": {}, "updated_utc": now.isoformat()}
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

@router.message(Command("coins"))
async def cmd_coins(msg: types.Message, command: CommandObject):
    args = (command.args or "").strip() if command else ""
    f = coins_file()
    if not args:
        return await msg.answer(mono(", ".join(read_coins()) or "(пусто)"))
    syms = [s for s in parse_csv(args) if SAFE.fullmatch(s)]
    f.write_text("\n".join(syms) + "\n", encoding="utf-8")
    return await msg.answer(mono(", ".join(syms) or "(пусто)"))

@router.message(Command("now"))
async def cmd_now(msg: types.Message, command: CommandObject):
    args = (command.args or "").strip() if command else ""
    syms = read_coins() if not args else parse_csv(args)
    syms = [s for s in syms if SAFE.fullmatch(s)]
    if not syms: return
    d = storage_dir()
    async def run(s: str):
        try: await run_now_for_symbol(s, str(d))
        except Exception: pass
    await asyncio.gather(*(run(s) for s in syms))
    return
