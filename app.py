
import os
from datetime import datetime, timezone
from fastapi import FastAPI, Request
import json
import httpx

from portfolio import build_portfolio_message, adjust_invested_total
from now_command import run_now
from range_mode import get_mode, set_mode, list_modes
from symbol_info import build_symbol_message

from budget import read_pair_budget, write_pair_budget, adjust_pair_budget, apply_budget_header, set_flag_override, cancel_flag_override

# --- budget-aware wrapper for symbol cards ---
try:
    _orig_build_symbol_message = build_symbol_message
    def build_symbol_message(symbol: str):
        card = _orig_build_symbol_message(symbol)
        return apply_budget_header(symbol, card)
except Exception:
    pass

BOT_TOKEN = os.getenv("TRAIDER_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("TRAIDER_ADMIN_CAHT_ID", "").strip()
WEBHOOK_BASE = os.getenv("TRAIDER_WEBHOOK_BASE") or os.getenv("WEBHOOK_BASE") or ""
METRIC_CHAT_ID = os.getenv("TRAIDER_METRIC_CHAT_ID", "").strip()
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

import json, re
from general_scheduler import start_collector, stop_collector, scheduler_get_state, scheduler_set_enabled, scheduler_set_timing, scheduler_tail

# === Coins config helpers ===
def _pairs_env() -> list[str]:
    raw = os.getenv("PAIRS", "") or ""
    raw = raw.strip()
    if not raw:
        return []
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    # dedup preserving order
    seen=set(); out=[]
    for s in parts:
        if s not in seen:
            seen.add(s); out.append(s)
    return out

def load_pairs(storage_dir: str = STORAGE_DIR) -> list[str]:
    path = os.path.join(storage_dir, "pairs.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            res=[]; seen=set()
            for x in data:
                s = str(x).strip().upper()
                if s and s not in seen:
                    seen.add(s); res.append(s)
            return res
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return []
# === end helpers ===


TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
app = FastAPI()
client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

def _log(*args):
    try:
        print("[bot]", *args, flush=True)
    except Exception:
        pass


async def tg_send(chat_id: str, text: str) -> None:
    if not TELEGRAM_API:
        _log("tg_send SKIP: TELEGRAM_API missing")
        return
    head = (text or "").splitlines()[0] if text else ""
    _log("tg_send try: len=", len(text or ""), "parse=Markdown", "head=", head[:140])
    try:
        r = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True},
        )
        try:
            j = r.json()
        except Exception:
            j = None
        if r.status_code != 200 or (j and not j.get("ok", True)):
            _log("tg_send markdown resp:", r.status_code, j or r.text[:200])
            # Fallback: send without Markdown
            _log("tg_send fallback: plain text")
            r2 = await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
            try:
                j2 = r2.json()
            except Exception:
                j2 = None
            _log("tg_send plain resp:", r2.status_code, j2 or r2.text[:200])
        else:
            _log("tg_send ok:", r.status_code)
    except Exception as e:
        _log("tg_send exception:", e.__class__.__name__, str(e)[:240])


async def _binance_ping() -> str:
    url = "https://api.binance.com/api/v3/ping"
    try:
        r = await client.get(url)
        return "✅" if r.status_code == 200 else f"❌ {r.status_code}"
    except Exception as e:
        return f"❌ {e.__class__.__name__}: {e}"

@app.on_event("startup")
async def on_startup():
    ping = await _binance_ping()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = f"{now_utc} Бот запущен\nBinance connection: {ping}"
    if ADMIN_CHAT_ID:
        await tg_send(ADMIN_CHAT_ID, msg)

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/telegram")
async def telegram_webhook(update: Request):
    try:
        data = await update.json()
    except Exception:
        data = {}
    message = data.get("message") or data.get("edited_message") or {}
    text = (message.get("text") or "").strip()
    text_norm = text
    text_lower = text_norm.lower()
    text_upper = text_norm.upper()
    chat_id = str((message.get("chat") or {}).get("id") or "")
    if not chat_id:
        return {"ok": True}

    if text_lower.startswith("/invested") or text_lower.startswith("/invest "):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            raw = parts[1].replace(",", ".")
            try:
                delta = float(raw)
                new_total = adjust_invested_total(STORAGE_DIR, delta)
                sign = "+" if delta >= 0 else ""
                reply = f"OK. Added: {sign}{delta:.2f}$ | Invested total: {new_total:.2f}$"
            except ValueError:
                reply = "Нужна сумма: /invested 530 или /invest -10"
        else:
            reply = "Нужна сумма: /invested 530"
        await tg_send(chat_id, _code(reply))
        return {"ok": True}

    
    if text_lower.startswith("/coins"):
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            pairs = load_pairs()
            reply = "Пары: " + (", ".join(pairs) if pairs else "—")
            await tg_send(chat_id, _code(reply))
            return {"ok": True}
        else:
            rest = parts[1].strip()
            items = [x.strip().upper() for x in rest.split() if x.strip()]
            valids = []
            invalids = []
            for sym in items:
                if re.fullmatch(r"[A-Z]+", sym) and sym.endswith("USDC"):
                    valids.append(sym)
                else:
                    invalids.append(sym)
            if invalids:
                await tg_send(chat_id, _code("Некорректные тикеры: " + ", ".join(invalids)))
                return {"ok": True}
            # dedup
            seen=set(); filtered=[]
            for s in valids:
                if s not in seen:
                    seen.add(s); filtered.append(s)
            save_pairs(filtered)
            await tg_send(chat_id, _code("Пары обновлены: " + (", ".join(filtered) if filtered else "—")))
            return {"ok": True}

    if text_lower.startswith("/now"):
        parts = (text or "").strip().split()
        mode_arg = None
        if len(parts) >= 2 and parts[1].strip().lower() in ("long","short"):
            mode_arg = parts[1].strip().upper()
        count, msg = await run_now(mode_arg)
        _log("/now result:", count)
        await tg_send(chat_id, _code(msg))
        # After update, send per-symbol messages (one message per ticker)
        try:
            pairs = load_pairs()
        except Exception:
            pairs = []
        # Filter pairs by mode if requested
        if mode_arg:
            try:
                filtered = []
                for _s in (pairs or []):
                    _, _m = get_mode(_s)
                    if _m == mode_arg:
                        filtered.append(_s)
                pairs = filtered
            except Exception:
                pass
        for sym in (pairs or []):
            try:
                smsg = build_symbol_message(sym)
                _log("/now symbol", sym, "len=", len(smsg or ""))
                await tg_send(chat_id, _code(smsg))
            except Exception:
                # Continue even if one symbol fails to render
                pass
        return {"ok": True}
    if text_lower.startswith("/budget"):
    # --- FLAG COMMANDS START ---
    if text_lower.startswith("/budget"):
        tokens = (text or "").split()
        # pattern: /budget <symbol> <target> <action>
        if len(tokens) >= 4:
            sym = tokens[1].lstrip("/").upper()
            target = tokens[2].lower()
            action = tokens[3].lower()
            if target in ("oco","l0","l1","l2","l3") and action in ("open","cancel","fill"):
                try:
                    if action == "open":
                        st = set_flag_override(sym, target, "open")
                        await tg_send(chat_id, _code(f"OK. FLAG[{sym}][{target.upper()}] = ⚠️"))
                    elif action == "fill":
                        st = set_flag_override(sym, target, "fill")
                        await tg_send(chat_id, _code(f"OK. FLAG[{sym}][{target.upper()}] = ✅"))
                    else:  # cancel
                        res = cancel_flag_override(sym, target)
                        if res == "fill":
                            await tg_send(chat_id, _code(f"SKIP. FLAG[{sym}][{target.upper()}] уже ✅"))
                        else:
                            await tg_send(chat_id, _code(f"OK. FLAG[{sym}][{target.upper()}] = AUTO"))
                    return {"ok": True}
                except Exception as e:
                    await tg_send(chat_id, _code(f"Ошибка: {e}"))
                    return {"ok": False}
    # --- FLAG COMMANDS END ---

        parts = (text or "").split(maxsplit=1)
        if len(parts) == 1 or not parts[1].strip():
            await tg_send(chat_id, _code("Формат: /budget SYMBOL=VALUE | SYMBOL +DELTA | SYMBOL -DELTA"))
            return {"ok": True}
        arg = parts[1].strip()
        arg = re.sub(r"\s+", " ", arg)
        m = re.match(r"^/?([a-z0-9_]+)\s*([=+\-])\s*([0-9]+(?:\.[0-9]+)?)$", arg, flags=re.I)
        if m is None:
            m = re.match(r"^/?([a-z0-9_]+)([=+\-])([0-9]+(?:\.[0-9]+)?)$", arg, flags=re.I)
        if not m:
            await tg_send(chat_id, _code("Не понял. Примеры: /budget btcusdc=25 | /budget btcusdc +5 | /budget btcusdc-3"))
            return {"ok": True}
        sym = m.group(1).upper()
        op  = m.group(2)
        val = float(m.group(3))
        cur = read_pair_budget(sym)
        if op == "=":
            newv = val
        elif op == "+":
            newv = cur + val
        else:
            newv = cur - val
        if newv < 0:
            newv = 0.0
        write_pair_budget(sym, newv)
        disp = int(newv) if abs(newv - int(newv)) < 1e-9 else round(newv, 6)
        await tg_send(chat_id, _code(f"OK. BUDGET[{sym}] = {disp}"))
        return {"ok": True}


    
    if text_lower.startswith("/mode"):
        parts = text.split()
        # /mode
        if len(parts) == 1:
            summary = list_modes()
            await tg_send(chat_id, _code(f"Режимы: {summary}"))
            return {"ok": True}
    

        # /mode <SYMBOL>
        if len(parts) == 2:
            sym, md = get_mode(parts[1])
            if not sym:
                await tg_send(chat_id, _code("Некорректная команда"))
                return {"ok": True}
            await tg_send(chat_id, _code(f"{sym}: {md}"))
            return {"ok": True}
        # /mode <SYMBOL> <LONG|SHORT>
        if len(parts) >= 3:
            sym = parts[1]
            md  = parts[2]
            try:
                sym, md = set_mode(sym, md)
                await tg_send(chat_id, _code(f"{sym} → {md}"))
            except ValueError:
                await tg_send(chat_id, _code("Некорректный режим"))
            return {"ok": True}

    
    # Symbol shortcut: /ETHUSDC, /BTCUSDC etc
    if text_lower.startswith("/") and len(text_norm) > 2:
        sym = text_upper[1:].split()[0].upper()
        # ignore known command prefixes
        if sym not in ("NOW","MODE","PORTFOLIO","COINS","DATA","JSON","INVESTED","INVEST","MARKET","SHEDULER"):
            msg = build_symbol_message(sym)
            await tg_send(chat_id, _code(msg))
            return {"ok": True}

    if text_lower.startswith("/market"):
        parts = text.split()
        # list all
        if len(parts) == 1:
            pairs = load_pairs()
            if not pairs:
                await tg_send(chat_id, _code("Пары: —"))
                return {"ok": True}
            lines = [_market_line_for(sym) for sym in pairs]
            await tg_send(chat_id, _code("\n".join(lines)))
            return {"ok": True}
        # specific symbol
        sym = parts[1].strip().upper()
        await tg_send(chat_id, _code(_market_line_for(sym)))
        return {"ok": True}

    
    
    if text_lower.startswith("/data"):
        parts = text.split()
        # /data -> list all files in STORAGE_DIR (any extension, non-recursive)
        if len(parts) == 1:
            files = sorted([os.path.basename(p) for p in glob.glob(os.path.join(STORAGE_DIR, "*")) if os.path.isfile(p)])
            msg = "Файлы: " + (", ".join(files) if files else "—")
            await tg_send(chat_id, _code(msg))
            return {"ok": True}
        # /data delete <NAME> -> delete file only if it exists in listing
        if len(parts) >= 3 and parts[1].strip().lower() == "delete":
            name = os.path.basename(parts[2].strip())
            files = sorted([os.path.basename(p) for p in glob.glob(os.path.join(STORAGE_DIR, "*")) if os.path.isfile(p)])
            if name not in files:
                await tg_send(chat_id, _code("Файл не найден"))
                return {"ok": True}
            path = os.path.join(STORAGE_DIR, name)
            try:
                os.remove(path)
                await tg_send(chat_id, _code(f"Удалено: {name}"))
            except Exception as e:
                await tg_send(chat_id, _code(f"Ошибка удаления: {name}: {e.__class__.__name__}"))
            return {"ok": True}
        # /data <NAME> -> send file as document
        name = os.path.basename(parts[1].strip())
        path = os.path.join(STORAGE_DIR, name)
        if not (os.path.exists(path) and os.path.isfile(path)):
            await tg_send(chat_id, _code("Файл не найден"))
            return {"ok": True}
        await tg_send_file(chat_id, path, filename=name, caption=name)
        return {"ok": True}


    
    
    if text_lower.startswith("/sheduler"):
        parts = (text or "").strip().split()
        # /sheduler config
        if len(parts) >= 2 and parts[1].lower() == "config":
            st = scheduler_get_state()
            await tg_send(chat_id, _code(json.dumps(st, ensure_ascii=False, indent=2)))
            return {"ok": True}
        # /sheduler on|off
        if len(parts) >= 2 and parts[1].lower() in ("on","off"):
            on = parts[1].lower() == "on"
            scheduler_set_enabled(on)
            if on:
                await start_collector()
            else:
                await stop_collector()
            await tg_send(chat_id, _code(f"Scheduler: {'ON' if on else 'OFF'}"))
            return {"ok": True}
        # /sheduler tail N
        if len(parts) >= 3 and parts[1].lower() == "tail":
            try:
                n = int(parts[2])
            except Exception:
                n = 100
            n = max(1, min(5000, n))
            tail_text = scheduler_tail(n)
            tmp_path = os.path.join(STORAGE_DIR, "scheduler_tail.txt")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(tail_text or "")
                await tg_send_file(chat_id, tmp_path, filename="scheduler_tail.txt", caption="scheduler_tail.txt")
            except Exception:
                await tg_send(chat_id, _code(tail_text or "—"))
            return {"ok": True}
        # /sheduler <interval> [jitter]
        if len(parts) >= 2 and parts[1].isdigit():
            interval = int(parts[1])
            jitter = None
            if len(parts) >= 3 and parts[2].isdigit():
                jitter = int(parts[2])
            # validation
            interval = max(15, min(43200, interval))
            if jitter is not None:
                jitter = max(1, min(5, jitter))
            st = scheduler_set_timing(interval, jitter)
            await tg_send(chat_id, _code("OK"))
            # If enabled, restart loop to apply quickly
            if st.get("enabled"):
                await stop_collector()
                await start_collector()
            return {"ok": True}
        await tg_send(chat_id, _code("Команды: /sheduler on|off | config | <sec> [jitter] | tail <N>"))
        return {"ok": True}
    if text_lower.startswith("/portfolio"):
        try:
            reply = await build_portfolio_message(client, BINANCE_API_KEY, BINANCE_API_SECRET, STORAGE_DIR)
            _log("/portfolio built", "len=", len(reply or ""), "head=", (reply or "").splitlines()[0][:160])
        except Exception as e:
            reply = f"Ошибка портфеля: {e}"
        await tg_send(chat_id, reply or "Нет данных.")
        return {"ok": True}

    return {"ok": True}


@app.get("/")
async def root():
    return {"ok": True, "service": "traider-bot"}


@app.head("/")
async def root_head():
    return {"ok": True}


@app.head("/health")
async def health_head():
    return {"ok": True}


# metrics collector moved to metrics_runner.py


@app.on_event("startup")
async def _startup_metrics():
    # start metrics collector in background (jittered)
    await start_collector()

@app.on_event("shutdown")
async def _shutdown_metrics():
    await stop_collector()


def _load_json_safe(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _market_line_for(symbol: str) -> str:
    path = os.path.join(STORAGE_DIR, f"{symbol}.json")
    data = _load_json_safe(path)
    trade_mode = str((data.get("trade_mode") or "SHORT")).upper()
    market_mode = str((data.get("market_mode") or "RANGE")).upper()
    # emojis
    mm_emoji = {"UP":"⬆️","DOWN":"⬇️","RANGE":"🔄"}.get(market_mode, "🔄")
    tm_emoji = {"LONG":"📈","SHORT":"📉"}.get(trade_mode, "")
    return f"{symbol} {market_mode}{mm_emoji} Mode {trade_mode}{tm_emoji}"


def _code(msg: str) -> str:
    return f"""```
{msg}
```"""


import glob

async def tg_send_file(chat_id: int, filepath: str, filename: str | None = None, caption: str | None = None):
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    _log("tg_send_file", filepath, "caption_len=", len(caption or ""))
    fn = filename or os.path.basename(filepath)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=20.0) as client:
            with open(filepath, "rb") as f:
                form = {"chat_id": str(chat_id)}
                files = {"document": (fn, f, "application/json")}
                if caption:
                    form["caption"] = caption
                r = await client.post(api_url, data=form, files=files)
                r.raise_for_status()
    except Exception:
        # silently ignore to avoid breaking webhook
        pass

def _save_json_atomic(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)

def read_pair_budget(symbol: str) -> float:
    try:
        data = _load_json_safe(os.path.join(STORAGE_DIR, f"{symbol}.json"))
        v = data.get("budget")
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str) and v.strip():
            return float(v.strip())
    except Exception:
        pass
    return 0.0

def write_pair_budget(symbol: str, value: float) -> float:
    path = os.path.join(STORAGE_DIR, f"{symbol}.json")
    data = _load_json_safe(path)
    data["budget"] = float(value)
    _save_json_atomic(path, data)
    return float(value)

def _apply_budget_header(symbol: str, msg: str) -> str:
    try:
        budget = read_pair_budget(symbol)
        lines = (msg or "").splitlines()
        # find first non-empty line; replace pure symbol line with "SYMBOL budget"
        for i, line in enumerate(lines):
            if line.strip() == "":
                continue
            if line.strip().upper() == symbol.upper():
                b = int(budget) if abs(budget - int(budget)) < 1e-9 else round(budget, 6)
                lines[i] = f"{symbol.upper()} {b}"
                break
            else:
                b = int(budget) if abs(budget - int(budget)) < 1e-9 else round(budget, 6)
                header = f"{symbol.upper()} {b}"
                return "\n".join([header] + lines)
        return "\n".join(lines)
    except Exception:
        return msg or ""
