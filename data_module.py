
import os, re
from pathlib import Path
from typing import List
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from utils import mono

router = Router()
SAFE = re.compile(r"^[a-z0-9._-]+$")

def storage_dir() -> Path:
    d = Path(os.getenv("STORAGE_DIR") or "./storage")
    d.mkdir(parents=True, exist_ok=True)
    return d

def list_files(d: Path) -> List[str]:
    res = []
    for p in sorted(d.iterdir()):
        if p.is_file():
            res.append(p.name)
    return res

def parse_csv(raw: str) -> List[str]:
    return [x.strip() for x in (raw.split(",") if raw else []) if x.strip()]

@router.message(Command("data"))
async def cmd_data(msg: types.Message, command: CommandObject):
    args = (command.args or "").strip() if command else ""
    if not args:
        names = list_files(storage_dir())
        return await msg.answer(mono(", ".join(names) if names else "(пусто)"))
    parts = args.split(None, 1)
    sub = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "export":
        items = list_files(storage_dir()) if rest.lower() == "all" else parse_csv(rest)
        return await msg.answer(mono(", ".join(items) if items else "(пусто)"))

    if sub == "delete":
        items = list_files(storage_dir()) if rest.lower() == "all" else parse_csv(rest)
        ok = []
        d = storage_dir()
        for name in items:
            if not SAFE.fullmatch(name): 
                continue
            p = d / name
            if p.exists() and p.is_file():
                try: p.unlink(); ok.append(name)
                except Exception: pass
        return await msg.answer(mono("deleted: " + ", ".join(ok) if ok else "(ничего)"))
