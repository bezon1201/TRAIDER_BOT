import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any

from aiogram import Router, types
from aiogram.filters import Command

router = Router()

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)

MARKET_PUBLISH_DEFAULT_HOURS = 24


def _symbols_file() -> Path:
    return STORAGE_PATH / "symbols_list.json"


def load_symbols() -> List[str]:
    path = _symbols_file()
    try:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        symbols = data.get("symbols", [])
        if not isinstance(symbols, list):
            return []
        result: List[str] = []
        for s in symbols:
            if not isinstance(s, str):
                continue
            s_up = s.upper()
            if s_up and s_up not in result:
                result.append(s_up)
        return result
    except Exception:
        return []


def _raw_market_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}raw_market.jsonl"


def _state_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}state.json"


def get_market_publish_hours() -> int:
    val = os.environ.get("MARKET_PUBLISH")
    if not val:
        return MARKET_PUBLISH_DEFAULT_HOURS
    try:
        hours = int(val)
        if hours <= 0:
            return MARKET_PUBLISH_DEFAULT_HOURS
        return hours
    except Exception:
        return MARKET_PUBLISH_DEFAULT_HOURS


def compute_market_mode(symbol: str, hours: int, now_ts: int) -> str:
    path = _raw_market_path(symbol)
    if not path.exists():
        return "RANGE"

    window_start = now_ts - hours * 3600
    up = down = rng = 0

    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = int(rec.get("ts", 0))
                if ts < window_start:
                    continue
                mode = rec.get("market_mode")
                if mode == "UP":
                    up += 1
                elif mode == "DOWN":
                    down += 1
                else:
                    rng += 1
    except Exception:
        return "RANGE"

    total = up + down + rng
    if total == 0:
        return "RANGE"

    if up / total > 0.5:
        return "UP"
    if down / total > 0.5:
        return "DOWN"
    return "RANGE"


def write_state(symbol: str, market_mode: str, now_ts: int) -> None:
    raw_path = STORAGE_PATH / f"{symbol}.json"
    raw_data: Dict[str, Any] = {}
    if raw_path.exists():
        try:
            with raw_path.open("r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception:
            raw_data = {}

    state: Dict[str, Any] = {}
    state["symbol"] = symbol
    state["tf1"] = raw_data.get("tf1")
    state["tf2"] = raw_data.get("tf2")
    state["updated_ts"] = now_ts
    state["market_mode"] = market_mode

    trading_params = raw_data.get("trading_params")
    if isinstance(trading_params, dict):
        state["trading_params"] = trading_params
    else:
        state["trading_params"] = {}

    path = _state_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


@router.message(Command("market"))
async def cmd_market(message: types.Message):
    text = message.text or ""
    parts = text.split()

    if len(parts) < 2 or parts[1].lower() != "force":
        await message.answer("Используйте: /market force")
        return

    symbols = load_symbols()
    if not symbols:
        await message.answer("В symbols_list нет ни одной пары.")
        return

    hours = get_market_publish_hours()
    now_ts = int(time.time())

    lines = []
    updated = 0
    for sym in symbols:
        mode = compute_market_mode(sym, hours, now_ts)
        write_state(sym, mode, now_ts)
        lines.append(f"{sym}: {mode}")
        updated += 1

    if updated == 0:
        await message.answer("Не удалось обновить state ни для одной пары.")
    else:
        body = "\n".join(lines)
        await message.answer(f"Обновили state для {updated} пар:\n{body}")
