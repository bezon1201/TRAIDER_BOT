import os
import re
from pathlib import Path
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from utils import mono

router = Router()

def normalize_symbol(s: str) -> str | None:
    s = (s or "").strip().lower()
    return s if s and re.fullmatch(r"[a-z0-9]+", s) else None


SAFE_SYMBOL_RE = re.compile(r"^[a-z0-9]+$")

def ensure_storage_dir(base: str | None = None) -> Path:
    d = Path(base or os.getenv("STORAGE_DIR") or "./storage")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d

def read_coins_file(d: Path) -> list[str]:
    f = d / "coins.txt"
    if not f.exists():
        return []
    try:
        lines = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()]
    except Exception:
        return []
    return [ln for ln in lines if ln]

def write_coins_file(d: Path, coins: list[str]) -> None:
    (d / "coins.txt").write_text("\n".join(coins) + "\n", encoding="utf-8")

def norm_and_filter(raw_symbols: list[str]) -> list[str]:
    out = []
    seen = set()
    for s in raw_symbols:
        s = (s or "").strip().lower()
        if not s:
            continue
        if not SAFE_SYMBOL_RE.match(s):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return sorted(out)

def parse_csv_args(raw: str) -> list[str]:
    parts = [p.strip() for p in (raw.split(",") if raw else [])]
    return [p for p in parts if p]

@router.message(Command("coins"))
async def cmd_coins(msg: types.Message, command: CommandObject):
    d = ensure_storage_dir()
    raw = (command.args or "").strip()

    # Show current list
    if not raw:
        coins = read_coins_file(d)
        text = "(пусто)" if not coins else ", ".join(coins)
        return await msg.answer(mono(text))

    # Set list
    items = parse_csv_args(raw)
    coins = norm_and_filter(items)
    write_coins_file(d, coins)
    text = "(пусто)" if not coins else ", ".join(coins)
    return await msg.answer(mono(f"ok: {text}"))

import asyncio
from metric_runner import run_now_for_symbol

@router.message(Command("now"))
async def cmd_now(msg: types.Message, command: CommandObject):
    d = ensure_storage_dir()
    raw = (command.args or "").strip()
    if not raw:
        # run for full list from coins.txt
        coins = read_coins_file(d)
        parts = coins
        # fall through

    # parse and normalize symbols
    parts = [p.strip() for p in raw.split(",") if p.strip()] if raw else parts
    symbols = []
    for p in parts:
        s = normalize_symbol(p)
        if s:
            symbols.append(s)
    # dedupe while preserving order
    seen = set(); uniq = []
    for s in symbols:
        if s not in seen:
            seen.add(s); uniq.append(s)

    if not uniq:
        return await msg.answer(mono("(пусто)"))

    # run per-symbol collection with per-task error isolation
    ok, bad = [], []
    async def runner(s: str):
        try:
            await run_now_for_symbol(s, str(d))
            ok.append(s)
        except Exception:
            bad.append(s)
    await asyncio.gather(*(runner(s) for s in uniq))

    return
