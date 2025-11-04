import os
import re
import json
from pathlib import Path
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request

from symbol_info import build_symbol_message
from now_command import run_now
from general_scheduler import (
    start_collector, stop_collector,
    scheduler_get_state, scheduler_set_enabled,
    scheduler_set_timing, scheduler_tail
)

from budget import (
    normalize_symbol, parse_budget_command, apply_budget_command,
    get_budget_for_symbol
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

app = FastAPI()

def _code(s: str) -> str:
    return f"```\n{s}\n```"

async def tg_send(chat_id: int, text: str, parse_mode: str = "Markdown"):
    if not BOT_TOKEN or not chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(f"{API_URL}/sendMessage", data={
                "chat_id": str(chat_id),
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": "true",
            })
    except Exception:
        pass

async def tg_send_file(chat_id: int, filepath: Path, caption: str = "", parse_mode: str = "Markdown"):
    if not BOT_TOKEN or not chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            with open(filepath, "rb") as f:
                files = {"document": (filepath.name, f, "text/plain")}
                data = {"chat_id": str(chat_id)}
                if caption:
                    data["caption"] = caption
                    data["parse_mode"] = parse_mode
                await client.post(f"{API_URL}/sendDocument", data=data, files=files)
    except Exception:
        pass

def _inject_budget(sym: str, msg: str) -> str:
    """Insert 'SYMBOL <budget>' as first line if budget exists."""
    try:
        budget = get_budget_for_symbol(sym)
    except Exception:
        budget = 0
    lines = msg.splitlines()
    header = (lines[0] if lines else sym.upper())
    head_sym = sym.upper()
    # If header already starts with symbol, append budget; else create a header line
    if lines:
        if not header.upper().startswith(head_sym):
            lines.insert(0, head_sym + (f" {budget}" if budget is not None else ""))
        else:
            # replace only the first token with "SYMBOL budget"
            parts = header.split()
            parts[0] = head_sym
            if budget is not None:
                if len(parts) == 1 or not parts[1].isdigit():
                    parts.insert(1, str(budget))
                else:
                    parts[1] = str(budget)
            lines[0] = " ".join(parts)
    else:
        lines = [f"{head_sym} {budget}"]
    return "\n".join(lines)

@app.get("/")
async def root():
    return {"ok": True}

@app.on_event("startup")
async def _startup():
    await tg_send(ADMIN_CHAT_ID, _code(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC Бот запущен"))
    await start_collector()

@app.on_event("shutdown")
async def _shutdown():
    await stop_collector()

@app.post("/telegram")
async def telegram_webhook(request: Request):
    payload = await request.json()
    msg = payload.get("message") or payload.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id", 0)
    text = (msg.get("text") or "").strip()
    text_lower = text.lower()

    if not text or not chat_id:
        return {"ok": True}

    # -------- /budget --------
    if text_lower.startswith("/budget"):
        from budget import parse_budget_command, apply_budget_command
        cmd = parse_budget_command(text)
        result = apply_budget_command(cmd)
        await tg_send(chat_id, _code(f"BUDGET\n{result}"))
        return {"ok": True}

    # -------- scheduler controls --------
    if text_lower.startswith("/sheduler") or text_lower.startswith("/scheduler"):
        # normalize typo /sheduler
        parts = text.split()
        if len(parts) == 1:
            st = scheduler_get_state()
            await tg_send(chat_id, _code(json.dumps(st, ensure_ascii=False, indent=2)))
            return {"ok": True}
        sub = parts[1].lower()
        if sub in ("on", "off"):
            scheduler_set_enabled(sub == "on")
            await tg_send(chat_id, _code(f"scheduler: {'ON' if sub=='on' else 'OFF'}"))
            return {"ok": True}
        if sub == "config":
            st = scheduler_get_state()
            await tg_send(chat_id, _code(json.dumps(st, ensure_ascii=False, indent=2)))
            return {"ok": True}
        if sub == "tail":
            n = int(parts[2]) if len(parts) > 2 else 200
            path = Path(os.getenv("STORAGE_DIR", ".")) / "scheduler_tail.txt"
            try:
                await tg_send_file(chat_id, path, caption=_code("scheduler tail"))
            except Exception:
                await tg_send(chat_id, _code("tail недоступен"))
            return {"ok": True}
        # numeric timing
        m = re.match(r"^/s[hc]eduler\s+(\d+)(?:\s+(\d+))?", text_lower)
        if m:
            sec = int(m.group(1))
            jit = int(m.group(2) or 3)
            ok, err = scheduler_set_timing(sec, jit)
            if ok:
                await tg_send(chat_id, _code(f"scheduler: {sec}s ±{jit}s"))
            else:
                await tg_send(chat_id, _code(f"ошибка: {err}"))
            return {"ok": True}

    # -------- /now --------
    if text_lower.startswith("/now"):
        mode = None
        if " long" in text_lower:
            mode = "long"
        elif " short" in text_lower:
            mode = "short"
        updated, messages = await run_now(mode)
        await tg_send(chat_id, _code(f"Обновлено: {updated}"))
        if isinstance(messages, str):
            messages = [messages]
        for m in messages:
            # try to detect symbol token (first word of first line)
            first_line = (m.splitlines() or [""])[0].strip()
            sym = first_line.split()[0] if first_line else ""
            if sym:
                m = _inject_budget(sym, m)
            await tg_send(chat_id, _code(m))
        return {"ok": True}

    # -------- single symbol like /btcusdc --------
    if text.startswith("/") and len(text) <= 16:
        bad_prefixes = ("now", "data", "help", "start", "sheduler", "scheduler", "json", "budget")
        name = text[1:]
        if name.lower() not in bad_prefixes and not any(name.lower().startswith(bp) for bp in bad_prefixes):
            sym = normalize_symbol(name)
            smsg = build_symbol_message(sym)
            smsg = _inject_budget(sym, smsg)
            await tg_send(chat_id, _code(smsg))
            return {"ok": True}

    # ignore everything else
    return {"ok": True}
