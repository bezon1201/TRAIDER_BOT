
import os, re, asyncio
from pathlib import Path
from typing import List
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from utils import mono
from metric_runner import run_now_for_symbol

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

@router.message(Command("coins"))
async def cmd_coins(msg: types.Message, command: CommandObject):
    args = (command.args or "").strip() if command else ""
    f = coins_file()
    if not args:
        return await msg.answer(mono(", ".join(read_coins()) or "(пусто)"))
    syms = []
    for s in parse_csv(args):
        if SAFE.fullmatch(s):
            syms.append(s)
    f.write_text("\n".join(syms) + "\n", encoding="utf-8")
    return await msg.answer(mono(", ".join(syms) or "(пусто)"))

@router.message(Command("now"))
async def cmd_now(msg: types.Message, command: CommandObject):
    args = (command.args or "").strip() if command else ""
    syms = read_coins() if not args else parse_csv(args)
    syms = [s for s in syms if SAFE.fullmatch(s)]
    if not syms: 
        return  # тихо
    d = storage_dir()
    ok = []
    async def run(s: str):
        try:
            await run_now_for_symbol(s, str(d))
            ok.append(s)
        except Exception:
            pass
    await asyncio.gather(*(run(s) for s in syms))
    return  # тихо
