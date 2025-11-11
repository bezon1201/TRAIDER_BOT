
import os, re, asyncio
from pathlib import Path
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from market_mode import evaluate_for_symbol, append_raw

router = Router()
SAFE = re.compile(r"^[a-z0-9]+$")

def storage_dir() -> Path:
    d = Path(os.getenv("STORAGE_DIR") or "./storage")
    d.mkdir(parents=True, exist_ok=True)
    return d

def coins_file() -> Path:
    return storage_dir() / "coins.txt"

def read_coins() -> list[str]:
    f = coins_file()
    if not f.exists(): return []
    return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]

def parse_csv(raw: str) -> list[str]:
    return [x.strip().lower() for x in (raw.split(",") if raw else []) if x.strip()]

@router.message(Command("market"))
async def cmd_market(msg: types.Message, command: CommandObject):
    from utils import mono
    args = (command.args or "").strip() if command else ""
    if not args:
        return
    parts = args.split(None, 1)
    sub = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "force":
        syms = read_coins() if not rest else parse_csv(rest)
        syms = [s for s in syms if SAFE.fullmatch(s)]
        if not syms: 
            return
        d = storage_dir()
        async def run(s: str):
            try:
                overall, tf_signals = evaluate_for_symbol(str(d), s)
                append_raw(str(d), s, overall, tf_signals)
            except Exception:
                pass
        await asyncio.gather(*(run(s) for s in syms))
        return

    if sub == "publish":
        from scheduler_module import publish_symbol, load_cfg
        from utils import mono
        try:
            ph = int(load_cfg().get("publish_hours") or 12)
        except Exception:
            ph = 12
        syms = read_coins() if not rest else parse_csv(rest)
        syms = [s for s in syms if SAFE.fullmatch(s)]
        if not syms:
            return await msg.answer(mono("(пусто)"))
        ok = []
        for s in syms:
            try:
                await publish_symbol(s, ph)
                ok.append(s)
            except Exception:
                pass
        return await msg.answer(mono("published: " + ", ".join(ok)))
