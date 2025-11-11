
import os
import json
import time
import asyncio
from typing import List, Tuple

import httpx
from fastapi import FastAPI, Request, Response, status

from data import handle_cmd_data
from metric_runner import (
    run_now_for_all,
    run_now_for_symbol,
    normalize_symbol,
    collect_symbol_metrics,
    write_json,
)
from market_mode import compute_overall_mode_from_metrics, append_raw_snapshot, publish_if_due
from scheduler import load_scheduler_cfg, save_scheduler_cfg, scheduler_defaults, human_period, human_hours

app = FastAPI()

TELEGRAM_API_BASE = "https://api.telegram.org"

def get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is not None:
        value = value.strip()
    return value or default

BOT_TOKEN = get_env("TRAIDER_BOT_TOKEN")
ADMIN_CHAT_ID = get_env("TRAIDER_ADMIN_CAHT_ID")
ADMIN_KEY = get_env("ADMIN_KEY")
WEBHOOK_BASE = get_env("WEBHOOK_BASE")
STORAGE_DIR = get_env("STORAGE_DIR", "/mnt/data")

COINS_FILE = os.path.join(STORAGE_DIR, "coins.txt")
SCHED_FILE = os.path.join(STORAGE_DIR, "scheduler.json")
SCHED_LOG = os.path.join(STORAGE_DIR, "scheduler_log.jsonl")

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

def normalize_symbol_local(token: str) -> str:
    t = "".join(ch for ch in (token or "").lower() if ch.isalnum())
    return t

def filter_symbols(raw: List[str]) -> Tuple[List[str], List[str]]:
    ok: List[str] = []
    bad: List[str] = []
    for tok in raw:
        t = normalize_symbol_local(tok)
        if not t:
            continue
        if not (t.endswith("usdt") or t.endswith("usdc")):
            bad.append(tok)
            continue
        ok.append(t)
    seen = set()
    ordered_ok = []
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

def write_coins(symbols: List[str]) -> None:
    ensure_storage()
    uniq = sorted(set([s.strip() for s in symbols if s.strip()]))
    text = "\n".join(uniq) + ("\n" if uniq else "")
    with open(COINS_FILE, "w", encoding="utf-8") as f:
        f.write(text)

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

def parse_command(text: str):
    if not text:
        return "", []
    t = text.strip()
    parts = t.split()
    if not parts:
        return "", []
    cmd = parts[0].casefold()
    args = parts[1:]
    return cmd, args

from scheduler import human_period, human_hours, load_scheduler_cfg, save_scheduler_cfg, scheduler_defaults

def sched_print(cfg: dict) -> str:
    return (
        "/scheduler\n"
        f"enabled: {cfg.get('enabled', True)}\n"
        f"period_sec: {cfg.get('period_sec')}   ({human_period(cfg.get('period_sec'))})\n"
        f"publish_hours: {cfg.get('publish_hours')}   ({human_hours(cfg.get('publish_hours'))})\n"
        f"delay_ms: {cfg.get('delay_ms')}\n"
        f"jitter_sec: {cfg.get('jitter_sec')}\n"
        f"last_run_utc: {cfg.get('last_run_utc')}\n"
        f"next_due_utc: {cfg.get('next_due_utc')}\n"
        f"last_publish_utc: {cfg.get('last_publish_utc')}\n"
        f"next_publish_utc: {cfg.get('next_publish_utc')}"
    )

async def handle_cmd_scheduler(chat_id: str, args: List[str]) -> None:
    cfg = load_scheduler_cfg(SCHED_FILE)
    if not args:
        await tg_send_message(chat_id, sched_print(cfg))
        return

    sub = (args[0] or "").casefold()
    def save_and_show():
        save_scheduler_cfg(SCHED_FILE, cfg)
        return sched_print(cfg)

    if sub == "period" and len(args) >= 2:
        try:
            sec = int(args[1])
        except ValueError:
            await tg_send_message(chat_id, "period must be integer seconds (60..86400)")
            return
        if not (60 <= sec <= 86400):
            await tg_send_message(chat_id, "period out of range (60..86400)")
            return
        cfg["period_sec"] = sec
        cfg["next_due_utc"] = None
        await tg_send_message(chat_id, save_and_show()); return

    if sub == "publish" and len(args) >= 2:
        try:
            hours = int(args[1])
        except ValueError:
            await tg_send_message(chat_id, "publish must be integer hours (12..72)")
            return
        if not (12 <= hours <= 72):
            await tg_send_message(chat_id, "publish out of range (12..72)")
            return
        cfg["publish_hours"] = hours
        cfg["next_publish_utc"] = None
        await tg_send_message(chat_id, save_and_show()); return

    if sub == "delay" and len(args) >= 2:
        try:
            ms = int(args[1])
        except ValueError:
            await tg_send_message(chat_id, "delay must be integer ms (1..3)")
            return
        if not (1 <= ms <= 3):
            await tg_send_message(chat_id, "delay out of range (1..3)")
            return
        cfg["delay_ms"] = ms
        await tg_send_message(chat_id, save_and_show()); return

    if sub == "jitter" and len(args) >= 2:
        try:
            js = int(args[1])
        except ValueError:
            await tg_send_message(chat_id, "jitter must be integer seconds (1..3)")
            return
        if not (1 <= js <= 3):
            await tg_send_message(chat_id, "jitter out of range (1..3)")
            return
        cfg["jitter_sec"] = js
        await tg_send_message(chat_id, save_and_show()); return

    if sub in ("on", "off"):
        cfg["enabled"] = (sub == "on")
        await tg_send_message(chat_id, save_and_show()); return

    await tg_send_message(chat_id, "Использование:\n"
                         "/scheduler\n"
                         "/scheduler period <sec> 60-86400\n"
                         "/scheduler publish <H> 12-72\n"
                         "/scheduler delay <ms>1-3\n"
                         "/scheduler jitter <sec>1-3\n"
                         "/scheduler on|off")

def _now_ts() -> int:
    return int(time.time())

def _log_scheduler(status: str, msg: str):
    rec = {"ts": _now_ts(), "status": status, "msg": msg}
    try:
        print(json.dumps(rec, ensure_ascii=False), flush=True)
    except Exception:
        pass
    try:
        with open(SCHED_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

async def _run_once_with_cfg(cfg: dict) -> tuple[int, int]:
    from market_mode import compute_overall_mode_from_metrics, append_raw_snapshot, publish_if_due
    from metric_runner import collect_symbol_metrics, write_json, normalize_symbol
    coins = read_coins()
    delay_ms = int(cfg.get("delay_ms", 2))
    processed = 0
    errors = 0
    for s in coins:
        s_norm = normalize_symbol(s)
        try:
            metrics = await collect_symbol_metrics(s_norm)
            write_json(STORAGE_DIR, s_norm, metrics)
            raw_mode, tf_signals = compute_overall_mode_from_metrics(metrics)
            append_raw_snapshot(STORAGE_DIR, s_norm, raw_mode, tf_signals)
            publish_if_due(STORAGE_DIR, s_norm, cfg)
            processed += 1
        except Exception as e:
            errors += 1
            _log_scheduler("error", f"{s_norm}:{e}")
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)
    return processed, errors

_bg_task = None
_bg_lock = asyncio.Lock()

async def _scheduler_loop():
    _log_scheduler("start", "scheduler loop started")
    while True:
        try:
            cfg = load_scheduler_cfg(SCHED_FILE) or scheduler_defaults()
            now = _now_ts()
            if cfg.get("next_due_utc") is None:
                cfg["next_due_utc"] = now + 2
            if cfg.get("next_publish_utc") is None:
                cfg["next_publish_utc"] = now + int(cfg.get("publish_hours", 24)) * 3600
            save_scheduler_cfg(SCHED_FILE, cfg)

            if not cfg.get("enabled", True):
                _log_scheduler("stop", "disabled")
                await asyncio.sleep(5)
                continue

            next_due = int(cfg.get("next_due_utc", now))
            if now >= next_due:
                _log_scheduler("start", "run begin")
                processed, errors = await _run_once_with_cfg(cfg)
                _log_scheduler("success", f"run ok; coins={processed}; errors={errors}")
                period = int(cfg.get("period_sec", 900))
                jitter = int(cfg.get("jitter_sec", 2))
                cfg["last_run_utc"] = now
                cfg["next_due_utc"] = now + period + (0 if jitter <= 0 else (now % (jitter + 1)))
                if cfg.get("next_publish_utc") is None:
                    cfg["next_publish_utc"] = now + int(cfg.get("publish_hours", 24)) * 3600
                save_scheduler_cfg(SCHED_FILE, cfg)
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(max(1, min(next_due - now, 30)))

        except asyncio.CancelledError:
            _log_scheduler("stop", "scheduler loop cancelled")
            raise
        except Exception as e:
            _log_scheduler("error", f"loop exception: {e}")
            await asyncio.sleep(3)

@app.on_event("startup")
async def on_startup() -> None:
    ensure_storage()
    if not os.path.exists(SCHED_FILE):
        save_scheduler_cfg(SCHED_FILE, scheduler_defaults())
    await tg_set_webhook()
    global _bg_task
    async with _bg_lock:
        if _bg_task is None or _bg_task.done():
            _bg_task = asyncio.create_task(_scheduler_loop())

@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _bg_task
    if _bg_task and not _bg_task.done():
        _bg_task.cancel()
        try:
            await _bg_task
        except asyncio.CancelledError:
            pass

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

    if cmd.startswith("/scheduler"):
        await handle_cmd_scheduler(chat_id, args)
        return Response(status_code=status.HTTP_200_OK)

    if cmd.startswith("/now"):
        if len(args) == 0:
            symbols = read_coins()
            await run_now_for_all(symbols, storage_dir=STORAGE_DIR)
        else:
            sym = normalize_symbol(args[0])
            await run_now_for_symbol(sym, storage_dir=STORAGE_DIR)
        return Response(status_code=status.HTTP_200_OK)

    return Response(status_code=status.HTTP_200_OK)

@app.post("/cron/run", include_in_schema=False)
async def cron_run(request: Request) -> Response:
    key = request.query_params.get("key") or ""
    if ADMIN_KEY and key != ADMIN_KEY:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    cfg = load_scheduler_cfg(SCHED_FILE) or scheduler_defaults()
    now = _now_ts()

    if not cfg.get("enabled", True):
        _log_scheduler("stop", "disabled (manual)")
        return Response(status_code=status.HTTP_200_OK)

    _log_scheduler("start", "manual run begin")
    processed, errors = await _run_once_with_cfg(cfg)
    _log_scheduler("success", f"manual run ok; coins={processed}; errors={errors}")

    period = int(cfg.get("period_sec", 900))
    jitter = int(cfg.get("jitter_sec", 2))
    cfg["last_run_utc"] = now
    cfg["next_due_utc"] = now + period + (0 if jitter <= 0 else (now % (jitter + 1)))
    if cfg.get("next_publish_utc") is None:
        cfg["next_publish_utc"] = now + int(cfg.get("publish_hours", 24)) * 3600
    save_scheduler_cfg(SCHED_FILE, cfg)

    return Response(status_code=status.HTTP_200_OK)
