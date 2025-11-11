import os, re, asyncio
from pathlib import Path
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from market_mode import evaluate_for_symbol, append_raw

router = Router()
SAFE_SYMBOL_RE = re.compile(r"^[a-z0-9]+$")

def ensure_storage_dir(base: str | None = None) -> Path:
    d = Path(base or os.getenv("STORAGE_DIR") or "./storage")
    try: d.mkdir(parents=True, exist_ok=True)
    except Exception: pass
    return d

def read_coins_file(d: Path) -> list[str]:
    f = d / "coins.txt"
    if not f.exists(): return []
    try: lines = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()]
    except Exception: return []
    return [ln for ln in lines if ln]

def normalize_symbol(s: str) -> str | None:
    s = (s or "").strip().lower()
    return s if s and SAFE_SYMBOL_RE.fullmatch(s) else None

def parse_symbols_csv(raw: str) -> list[str]:
    return [p.strip() for p in (raw.split(",") if raw else []) if p.strip()]

@router.message(Command("market"))
async def cmd_market(msg: types.Message, command: CommandObject):
    raw = (command.args or "").strip() if command else ""
    if not raw: return
    parts = raw.split(None, 1)
    sub = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    if sub != "force": return

    d = ensure_storage_dir()
    syms = read_coins_file(d) if not rest else parse_symbols_csv(rest)

    seen, symbols = set(), []
    for p in syms:
        s = normalize_symbol(p)
        if s and s not in seen:
            seen.add(s); symbols.append(s)
    if not symbols: return

    async def runner(sym: str):
        try:
            overall, tf_signals = evaluate_for_symbol(str(d), sym)
            append_raw(str(d), sym, overall, tf_signals)
        except Exception:
            pass

    await asyncio.gather(*(runner(s) for s in symbols))
    return
