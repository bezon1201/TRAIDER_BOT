import os
from typing import List, Tuple, Set

import httpx
from fastapi import FastAPI, Request, Response, status

from data import handle_cmd_data

app = FastAPI()

TELEGRAM_API_BASE = "https://api.telegram.org"

def get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is not None:
        value = value.strip()
    return value or default

BOT_TOKEN = get_env("TRAIDER_BOT_TOKEN")
ADMIN_CHAT_ID = get_env("TRAIDER_ADMIN_CAHT_ID")  # не используем для /coins
ADMIN_KEY = get_env("ADMIN_KEY")
WEBHOOK_BASE = get_env("WEBHOOK_BASE")
STORAGE_DIR = get_env("STORAGE_DIR", "/mnt/data")

COINS_FILE = os.path.join(STORAGE_DIR, "coins.txt")

async def tg_send_message(chat_id: str, text: str) -> None:
    if not BOT_TOKEN or not chat_id:
        return
    url = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            await client.post(url, json=payload)
        except Exception:
            pass

async def tg_set_webhook() -> None:
    if not BOT_TOKEN or not WEBHOOK_BASE:
        return
    url = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/setWebhook"
    webhook_url = WEBHOOK_BASE.rstrip("/") + "/webhook"
    payload = {"url": webhook_url, "allowed_updates": ["message", "callback_query"]}
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            await client.post(url, json=payload)
        except Exception:
            pass

def ensure_storage() -> None:
    try:
        os.makedirs(STORAGE_DIR, exist_ok=True)
    except Exception:
        pass

def read_coins() -> List[str]:
    if not os.path.exists(COINS_FILE):
        return []
    try:
        with open(COINS_FILE, "r", encoding="utf-8") as f:
            items = [line.strip() for line in f.readlines() if line.strip()]
        uniq = sorted(set(items))
        return uniq
    except Exception:
        return []

def write_coins(symbols: List[str]) -> None:
    ensure_storage()
    uniq = sorted(set([s.strip() for s in symbols if s.strip()]))
    text = "\n".join(uniq) + ("\n" if uniq else "")
    with open(COINS_FILE, "w", encoding="utf-8") as f:
        f.write(text)

def normalize_symbol(token: str) -> str:
    t = "".join(ch for ch in token.lower() if ch.isalnum())
    return t

def filter_symbols(raw: List[str]) -> Tuple[List[str], List[str]]:
    ok: List[str] = []
    bad: List[str] = []
    for tok in raw:
        t = normalize_symbol(tok)
        if not t:
            continue
        if not (t.endswith("usdt") or t.endswith("usdc")):
            bad.append(tok)
            continue
        ok.append(t)
    seen: Set[str] = set()
    ordered_ok: List[str] = []
    for s in ok:
        if s not in seen:
            seen.add(s)
            ordered_ok.append(s)
    return ordered_ok, bad

def format_coins_list(coins: List[str], title: str) -> str:
    if not coins:
        return f"{title}\n(список пуст)"
    lines = ["{0} ({1}):".format(title, len(coins))]
    lines.extend(coins)
    return "\n".join(lines)

async def handle_cmd_coins_show(chat_id: str) -> None:
    coins = read_coins()
    text = format_coins_list(coins, "/coins — текущий список")
    await tg_send_message(chat_id, text)

async def handle_cmd_coins_add(chat_id: str, args: List[str]) -> None:
    if not args:
        await tg_send_message(chat_id, "/coins +add — не переданы символы")
        return
    ok, bad = filter_symbols(args)
    if not ok and bad:
        await tg_send_message(chat_id, f"/coins +add — ничего не добавлено\nпропущено: {', '.join(bad)}")
        return
    coins = read_coins()
    new_set = sorted(set(coins).union(ok))
    write_coins(new_set)
    msg_lines = [format_coins_list(new_set, "/coins — обновлено (+add)")]
    if bad:
        msg_lines.append(f"пропущено: {', '.join(bad)}")
    await tg_send_message(chat_id, "\n\n".join(msg_lines))

async def handle_cmd_coins_rm(chat_id: str, args: List[str]) -> None:
    if not args:
        await tg_send_message(chat_id, "/coins +rm — не переданы символы")
        return
    ok, bad = filter_symbols(args)
    coins = read_coins()
    if ok:
        new_set = [s for s in coins if s not in set(ok)]
        write_coins(new_set)
    else:
        new_set = coins
    msg_lines = [format_coins_list(new_set, "/coins — обновлено (+rm)")]
    if ok:
        msg_lines.append(f"удалено: {', '.join(ok)}")
    if bad:
        msg_lines.append(f"пропущено: {', '.join(bad)}")
    await tg_send_message(chat_id, "\n\n".join(msg_lines))

def parse_command(text: str) -> Tuple[str, List[str]]:
    if not text:
        return "", []
    t = text.strip()
    parts = t.split()
    if not parts:
        return "", []
    cmd = parts[0].casefold()
    args = parts[1:]
    return cmd, args

@app.on_event("startup")
async def on_startup() -> None:
    ensure_storage()
    await tg_set_webhook()
    if ADMIN_CHAT_ID:
        text = "🤖 Trader bot skeleton started."
        if ADMIN_KEY:
            text += f" Admin key: {ADMIN_KEY}"
        await tg_send_message(ADMIN_CHAT_ID, text)

@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
async def healthcheck() -> Response:
    return Response(status_code=status.HTTP_200_OK, content="ok")

@app.post("/webhook", include_in_schema=False)
async def telegram_webhook(request: Request) -> Response:
    try:
        update = await request.json()
    except Exception:
        return Response(status_code=status.HTTP_200_OK)

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    text = message.get("text") or ""

    if not chat_id or not text:
        return Response(status_code=status.HTTP_200_OK)

    cmd, args = parse_command(text)

    if cmd.startswith("/coins"):
        if len(args) == 0:
            await handle_cmd_coins_show(chat_id)
        else:
            flag = args[0].casefold()
            rest = args[1:]
            if flag in ("+add", "add"):
                await handle_cmd_coins_add(chat_id, rest)
            elif flag in ("+rm", "rm", "-rm", "remove", "del", "delete"):
                await handle_cmd_coins_rm(chat_id, rest)
            else:
                await tg_send_message(chat_id, "Использование:\n/coins — показать\n/coins +add <symbols...>\n/coins +rm <symbols...>")
        return Response(status_code=status.HTTP_200_OK)

    if cmd.startswith("/data"):
        await handle_cmd_data(chat_id, args)
        return Response(status_code=status.HTTP_200_OK)

    return Response(status_code=status.HTTP_200_OK)
