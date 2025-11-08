import os
import glob
import re
import json
from datetime import datetime, timezone
from typing import Dict

import httpx
from fastapi import FastAPI, Request

from portfolio import build_portfolio_message, adjust_invested_total
from now_command import run_now
from range_mode import get_mode, set_mode, list_modes
from symbol_info import build_symbol_message
from orders import (
    prepare_open_oco,
    confirm_open_oco,
    prepare_open_l0,
    confirm_open_l0,
    prepare_open_l1,
    confirm_open_l1,
    prepare_open_l2,
    confirm_open_l2,
    prepare_open_l3,
    confirm_open_l3,
    prepare_cancel_oco,
    confirm_cancel_oco,
    prepare_cancel_l0,
    confirm_cancel_l0,
    prepare_cancel_l1,
    confirm_cancel_l1,
    prepare_cancel_l2,
    confirm_cancel_l2,
    prepare_cancel_l3,
    confirm_cancel_l3,
    prepare_fill_oco,
    confirm_fill_oco,
    prepare_fill_l0,
    confirm_fill_l0,
    prepare_fill_l1,
    confirm_fill_l1,
    prepare_fill_l2,
    confirm_fill_l2,
    prepare_fill_l3,
    confirm_fill_l3,
    perform_rollover,
    recompute_flags_for_symbol,
)
from general_scheduler import (
    start_collector,
    stop_collector,
    scheduler_get_state,
    scheduler_set_enabled,
    scheduler_set_timing,
    scheduler_tail,
)
from budget import (
    get_pair_budget,
    set_pair_budget,
    clear_pair_budget,
    set_pair_week,
    get_budget_input,
    set_budget_input,
    clear_budget_input,
    get_pair_levels,
    recompute_pair_aggregates,
    save_pair_levels,
)



# Процентное распределение бюджета по режимам рынка (на одну неделю)
# ДОЛЖНО совпадать с WEEKLY_PERCENT в coin_long_format.py
WEEKLY_PERCENT = {
    "UP": {
        "OCO": 10,
        "L0": 10,
        "L1": 5,
        "L2": 0,
        "L3": 0,
    },
    "RANGE": {
        "OCO": 5,
        "L0": 5,
        "L1": 10,
        "L2": 5,
        "L3": 0,
    },
    "DOWN": {
        "OCO": 5,
        "L0": 0,
        "L1": 5,
        "L2": 10,
        "L3": 5,
    },
}


def _symbol_data_path(symbol: str) -> str:
    storage_dir = os.getenv("STORAGE_DIR", "/data")
    return os.path.join(storage_dir, f"{symbol}.json")


def _load_symbol_data(symbol: str) -> dict:
    try:
        with open(_symbol_data_path(symbol), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

# =========================
# Sticker → Command mapping
# =========================
STICKER_TO_COMMAND: Dict[str, str] = {
    # BTC (из «избранного» / классический)
    "AgADXXoAAmI4WEg": "/now btcusdc",
    "CAACAgIAAxkBAAE9cZBpC455Ia8n2PR-BoR6niG4gykRTAACXXoAAmI4WEg5O5Gu6FBfMzYE": "/now btcusdc",

    # BTC (из пака traider_crypto_bot / «недавние»)
    "AgADJogAAtfnYUg": "/now btcusdc",
    "CAACAgIAAxkBAAE9dPtpDAnY_j75m55h8ctPgwzLP4fy8gACJogAAtfnYUiiLR_pVyWZPTYE": "/now btcusdc",

    # ETH
    "AgADxokAAv_wWEg": "/now ethusdc",
    "CAACAgIAAxkBAAE9ddhpDCyOcuY8oEj0_mPe_E1zbEa-ogACxokAAv_wWEir8uUsEqgkvDYE": "/now ethusdc",

    # BNB
    "AgADJocAAka7YUg": "/now bnbusdc",
    "CAACAgIAAxkBAAE9djtpDD842Hiibb4OWsspe5QgYvQsgwACJocAAka7YUijem2oBO1AazYE": "/now bnbusdc",

    # Portfolio (твои 2 ID из сообщения)
    "AgADDX0AAm5wYUg": "/portfolio",
    "CAACAgIAAxkBAAE9dm5pDEOSIjmsFXzC5bwkdNhHG_GJ7wACDX0AAm5wYUhMMGz5tJzGITYE": "/portfolio",
}

BOT_TOKEN = os.getenv("TRAIDER_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("TRAIDER_ADMIN_CAHT_ID", "").strip()
WEBHOOK_BASE = os.getenv("TRAIDER_WEBHOOK_BASE") or os.getenv("WEBHOOK_BASE") or ""
METRIC_CHAT_ID = os.getenv("TRAIDER_METRIC_CHAT_ID", "").strip()
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
STORAGE_DIR = os.getenv("STORAGE_DIR", "/data")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""
app = FastAPI()
client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)


def _log(*args):
    try:
        print("[bot]", *args, flush=True)
    except Exception:
        pass


def _code(msg: str) -> str:
    return f"""```
{msg}
```"""


def _load_json_safe(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _pairs_env() -> list[str]:
    raw = os.getenv("PAIRS", "") or ""
    raw = raw.strip()
    if not raw:
        return []
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    seen = set()
    out: list[str] = []
    for s in parts:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out



def load_pairs(storage_dir: str = STORAGE_DIR) -> list[str]:
    """
    Read active pairs from STORAGE_DIR/pairs.json.
    Supports:
      - {"pairs": ["BTCUSDC", ...]}
      - ["BTCUSDC", ...]  (legacy)
    Returns a de-duplicated UPPERCASE list preserving input order.
    """
    path = os.path.join(storage_dir, "pairs.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("pairs"), list):
            src = data.get("pairs", [])
        elif isinstance(data, list):
            src = data
        else:
            src = []
        seen = set()
        out: list[str] = []
        for x in src:
            s = str(x).strip().upper()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out
    except FileNotFoundError:
        return []
    except Exception:
        return []

def save_pairs_json(pairs: list[str], storage_dir: str = STORAGE_DIR) -> None:
    """Atomically write pairs.json as {"pairs":[...]}, ensuring directory exists."""
    path = os.path.join(storage_dir, "pairs.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {"pairs": list(pairs)}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


async def tg_send(chat_id: str, text: str, reply_markup: dict | None = None) -> None:
    if not TELEGRAM_API:
        _log("tg_send SKIP: TELEGRAM_API missing")
        return
    head = (text or "").splitlines()[0] if text else ""
    _log("tg_send try: len=", len(text or ""), "parse=Markdown", "head=", head[:140])
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        r = await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json=payload,
        )
        try:
            j = r.json()
        except Exception:
            j = None
        if r.status_code != 200 or (j and not j.get("ok", True)):
            _log("tg_send markdown resp:", r.status_code, j or r.text[:200])
            # Fallback: plain text
            _log("tg_send fallback: plain text")
            payload2 = {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            }
            if reply_markup is not None:
                payload2["reply_markup"] = reply_markup
            r2 = await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json=payload2,
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



async def tg_send_file(chat_id: int, filepath: str, filename: str | None = None, caption: str | None = None):
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    _log("tg_send_file", filepath, "caption_len=", len(caption or ""))
    fn = filename or os.path.basename(filepath)
    try:
        async with httpx.AsyncClient(timeout=20.0) as _client:
            with open(filepath, "rb") as f:
                form = {"chat_id": str(chat_id)}
                files = {"document": (fn, f, "application/json")}
                if caption:
                    form["caption"] = caption
                r = await _client.post(api_url, data=form, files=files)
                r.raise_for_status()
    except Exception:
        pass

async def _answer_callback(callback: dict) -> dict:
    """
    Handle inline keyboard callbacks for budget management.
    """
    data = str(callback.get("data") or "")
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return {"ok": True}

    # Stop Telegram's loading spinner (best-effort)
    cb_id = callback.get("id")
    if TELEGRAM_API and cb_id:
        try:
            await client.post(
                f"{TELEGRAM_API}/answerCallbackQuery",
                json={"callback_query_id": cb_id},
            )
        except Exception:
            pass

    data = data.strip()
    if not data:
        return {"ok": True}

    # Helper to edit reply markup on the original message
    async def _edit_markup(reply_markup: dict | None) -> None:
        msg_id = message.get("message_id")
        if not msg_id or not TELEGRAM_API:
            return
        try:
            payload = {
                "chat_id": chat_id,
                "message_id": msg_id,
                "reply_markup": reply_markup,
            }
            await client.post(
                f"{TELEGRAM_API}/editMessageReplyMarkup",
                json=payload,
            )
        except Exception:
            pass

    # Parse commands
    if data.startswith("BUDGET_SET:") or data.startswith("BUDGET_CLEAR:") or data.startswith("BUDGET_START:") or data.startswith("BUDGET_ROLLOVER:") or data.startswith("BUDGET:") or data.startswith("BUDGET_BACK_ROOT:") or data.startswith("ORDERS"):
        # Extract symbol
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}

        # Current month key YYYY-MM
        from datetime import datetime
        month = datetime.now().strftime("%Y-%m")

        # Main "BUDGET" button → show submenu
        
        if data.startswith("BUDGET:"):
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "SET", "callback_data": f"BUDGET_SET:{symbol}"},
                        {"text": "CANCEL", "callback_data": f"BUDGET_CLEAR:{symbol}"},
                        {"text": "START", "callback_data": f"BUDGET_START:{symbol}"},
                        {"text": "ROLLOVER", "callback_data": f"BUDGET_ROLLOVER:{symbol}"},
                    ],
                    [
                        {"text": "↩️", "callback_data": f"BUDGET_BACK_ROOT:{symbol}"},
                    ],
                ]
            }
            await _edit_markup(kb)
            return {"ok": True}


        # BUDGET back → восстановить корневое меню BUDGET / ORDERS
        if data.startswith("BUDGET_BACK_ROOT:"):
            try:
                _, sym_raw = data.split(":", 1)
            except ValueError:
                return {"ok": True}
            symbol = (sym_raw or "").upper().strip()
            if not symbol:
                return {"ok": True}
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "BUDGET", "callback_data": f"BUDGET:{symbol}"},
                        {"text": "ORDERS", "callback_data": f"ORDERS:{symbol}"},
                    ]
                ]
            }
            await _edit_markup(kb)
            return {"ok": True}
        # SET BUDGET → ask for value and restore single BUDGET button
        if data.startswith("BUDGET_SET:"):
            # store state: this chat is entering budget for this symbol/month
            set_budget_input(chat_id, symbol, month)
            msg = f"{symbol}\nВведите бюджет на месяц {month} в USDC (целым числом ≥ 0):"
            await tg_send(chat_id, _code(msg))
            # restore single BUDGET button on the card
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "BUDGET", "callback_data": f"BUDGET:{symbol}"},
                        {"text": "ORDERS", "callback_data": f"ORDERS:{symbol}"},
                    ]
                ]
            }
            await _edit_markup(kb)
            return {"ok": True}


        # BUDGET START → установить неделю цикла = 1 и показать карточку
        if data.startswith("BUDGET_START:"):
            info = set_pair_week(symbol, month, 1)
            # отправляем обновлённую карточку по символу
            try:
                sym = info.get("symbol") or symbol
                card = build_symbol_message(sym)
                kb = {
                    "inline_keyboard": [
                        [
                            {"text": "BUDGET", "callback_data": f"BUDGET:{sym}"},
                            {"text": "ORDERS", "callback_data": f"ORDERS:{sym}"},
                        ]
                    ]
                }
                await tg_send(chat_id, _code(card), reply_markup=kb)
            except Exception:
                pass
            # обновляем клавиатуру на исходном сообщении
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "BUDGET", "callback_data": f"BUDGET:{symbol}"},
                        {"text": "ORDERS", "callback_data": f"ORDERS:{symbol}"},
                    ]
                ]
            }
            await _edit_markup(kb)
            return {"ok": True}

        # BUDGET ROLLOVER → ролловер недели: снять ордера, перерасчитать квоты и увеличить week
        if data.startswith("BUDGET_ROLLOVER:"):
            info = perform_rollover(symbol)
            # отправляем обновлённую карточку по символу
            try:
                sym = (info or {}).get("symbol") or symbol
                card = build_symbol_message(sym)
                kb = {
                    "inline_keyboard": [
                        [
                            {"text": "BUDGET", "callback_data": f"BUDGET:{sym}"},
                            {"text": "ORDERS", "callback_data": f"ORDERS:{sym}"},
                        ]
                    ]
                }
                await tg_send(chat_id, _code(card), reply_markup=kb)
            except Exception:
                pass
            # обновляем клавиатуру на исходном сообщении
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "BUDGET", "callback_data": f"BUDGET:{symbol}"},
                        {"text": "ORDERS", "callback_data": f"ORDERS:{symbol}"},
                    ]
                ]
            }
            await _edit_markup(kb)
            return {"ok": True}

        # BUDGET CANCEL → reset reserve and spent, keep budget, restore single BUDGET button
        
        if data.startswith("BUDGET_CLEAR:"):
            info = clear_pair_budget(symbol, month)
            # после полного сброса пересчитаем флаги, чтобы снять ⚠️/✅
            try:
                recompute_flags_for_symbol(symbol)
            except Exception:
                pass
            # отправляем обновлённую карточку по символу
            try:
                sym = info.get("symbol") or symbol
                card = build_symbol_message(sym)
                kb = {
                    "inline_keyboard": [
                        [
                            {"text": "BUDGET", "callback_data": f"BUDGET:{sym}"},
                            {"text": "ORDERS", "callback_data": f"ORDERS:{sym}"},
                        ]
                    ]
                }
                await tg_send(chat_id, _code(card), reply_markup=kb)
            except Exception:
                pass
            # обновляем клавиатуру на исходном сообщении
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "BUDGET", "callback_data": f"BUDGET:{symbol}"},
                    ]
                ]
            }
            await _edit_markup(kb)
            # also clear any pending input for this chat
            clear_budget_input(chat_id)
            return {"ok": True}


    # ORDERS submenu: show OPEN / CANCEL / FILL and back to root
    if data.startswith("ORDERS:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OPEN", "callback_data": f"ORDERS_OPEN:{symbol}"},
                    {"text": "CANCEL", "callback_data": f"ORDERS_CANCEL:{symbol}"},
                    {"text": "FILL", "callback_data": f"ORDERS_FILL:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_ROOT:{symbol}"},
                ],
            ]
        }
        await _edit_markup(kb)
        return {"ok": True}

    # ORDERS → OPEN → подуровни OCO / L0-3 (пока только кнопки)
    if data.startswith("ORDERS_OPEN:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_OPEN_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_OPEN_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_OPEN_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_OPEN_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_OPEN_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        await _edit_markup(kb)
        return {"ok": True}
    # ORDERS → OPEN → OCO (подтверждение виртуального ордера)
    if data.startswith("ORDERS_OPEN_OCO:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        msg, kb = prepare_open_oco(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb if kb else None)
        return {"ok": True}

    # ORDERS → OPEN → подтверждение OCO
    if data.startswith("ORDERS_OPEN_OCO_CONFIRM:"):
        try:
            _, payload = data.split(":", 1)
            sym_raw, amount_raw = payload.split(":", 1)
        except ValueError:
            return {"ok": True}
    # ORDERS → OPEN → LIMIT 0 (подтверждение виртуального ордера)
    if data.startswith("ORDERS_OPEN_L0:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        msg, kb = prepare_open_l0(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb if kb else None)
        await _edit_markup(kb)
        return {"ok": True}

    # ORDERS → OPEN → подтверждение LIMIT 0
    if data.startswith("ORDERS_OPEN_L0_CONFIRM:"):
        try:
            _, sym, amount_str = data.split(":", 2)
        except ValueError:
            return {"ok": True}
        symbol = (sym or "").upper().strip()
        try:
            amount = int(amount_str)
        except Exception:
            amount = 0
        if not symbol or amount <= 0:
            return {"ok": True}
        msg, kb = confirm_open_l0(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb if kb else None)
        return {"ok": True}

    # ORDERS → OPEN → LIMIT 1 (подтверждение виртуального ордера)
    if data.startswith("ORDERS_OPEN_L1:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        msg, kb = prepare_open_l1(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb if kb else None)
        return {"ok": True}

    # ORDERS → OPEN → подтверждение LIMIT 1
    if data.startswith("ORDERS_OPEN_L1_CONFIRM:"):
        try:
            _, sym, amount_str = data.split(":", 2)
        except ValueError:
            return {"ok": True}
        symbol = (sym or "").upper().strip()
        try:
            amount = int(amount_str)
        except Exception:
            amount = 0
        if not symbol or amount <= 0:
            return {"ok": True}
        msg, kb = confirm_open_l1(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb if kb else None)
        return {"ok": True}

    # ORDERS → OPEN → LIMIT 2 (подтверждение виртуального ордера)
    if data.startswith("ORDERS_OPEN_L2:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        msg, kb = prepare_open_l2(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb if kb else None)
        return {"ok": True}

    # ORDERS → OPEN → подтверждение LIMIT 2
    if data.startswith("ORDERS_OPEN_L2_CONFIRM:"):
        try:
            _, sym, amount_str = data.split(":", 2)
        except ValueError:
            return {"ok": True}
        symbol = (sym or "").upper().strip()
        try:
            amount = int(amount_str)
        except Exception:
            amount = 0
        if not symbol or amount <= 0:
            return {"ok": True}
        msg, kb = confirm_open_l2(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb if kb else None)
        return {"ok": True}

    # ORDERS → OPEN → LIMIT 3 (подтверждение виртуального ордера)
    if data.startswith("ORDERS_OPEN_L3:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        msg, kb = prepare_open_l3(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb if kb else None)
        return {"ok": True}

    # ORDERS → OPEN → подтверждение LIMIT 3
    if data.startswith("ORDERS_OPEN_L3_CONFIRM:"):
        try:
            _, sym, amount_str = data.split(":", 2)
        except ValueError:
            return {"ok": True}
        symbol = (sym or "").upper().strip()
        try:
            amount = int(amount_str)
        except Exception:
            amount = 0
        if not symbol or amount <= 0:
            return {"ok": True}
        msg, kb = confirm_open_l3(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb if kb else None)
        return {"ok": True}

        symbol = (sym_raw or "").upper().strip()
        try:
            amount = int(amount_raw)
        except Exception:
            amount = 0
        if not symbol or amount <= 0:
            return {"ok": True}
        msg, kb = confirm_open_oco(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb if kb else None)
        return {"ok": True}

   # ORDERS → OPEN → подтверждение OCO
    if data.startswith("ORDERS_OPEN_OCO_CONFIRM:"):
        try:
            _, payload = data.split(":", 1)
            sym_raw, amount_raw = payload.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        try:
            amount = int(amount_raw)
        except Exception:
            amount = 0
        if not symbol or amount <= 0:
            return {"ok": True}
        msg, kb = confirm_open_oco(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb if kb else None)
        return {"ok": True}
    # ORDERS → CANCEL → выбор уровня для отмены
    if data.startswith("ORDERS_CANCEL:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        await _edit_markup(kb)
        return {"ok": True}

    # ORDERS → CANCEL → подготовка отмены OCO
    if data.startswith("ORDERS_CANCEL_OCO:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        msg, kb = prepare_cancel_oco(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb or None)
        return {"ok": True}

    # ORDERS → CANCEL → подготовка отмены LIMIT 0
    if data.startswith("ORDERS_CANCEL_L0:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        msg, kb = prepare_cancel_l0(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb or None)
        return {"ok": True}

    # ORDERS → CANCEL → подготовка отмены LIMIT 1
    if data.startswith("ORDERS_CANCEL_L1:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        msg, kb = prepare_cancel_l1(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb or None)
        return {"ok": True}

    # ORDERS → CANCEL → подготовка отмены LIMIT 2
    if data.startswith("ORDERS_CANCEL_L2:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        msg, kb = prepare_cancel_l2(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb or None)
        return {"ok": True}

    # ORDERS → CANCEL → подготовка отмены LIMIT 3
    if data.startswith("ORDERS_CANCEL_L3:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        msg, kb = prepare_cancel_l3(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb or None)
        return {"ok": True}

    # ORDERS → CANCEL → подтверждение OCO
    if data.startswith("ORDERS_CANCEL_OCO_CONFIRM:"):
        try:
            _, sym, amount_str = data.split(":", 2)
        except ValueError:
            return {"ok": True}
        symbol = (sym or "").upper().strip()
        if not symbol:
            return {"ok": True}
        try:
            amount = int(amount_str)
        except Exception:
            amount = 0
        msg, kb = confirm_cancel_oco(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb or None)
        return {"ok": True}

    # ORDERS → CANCEL → подтверждение LIMIT 0
    if data.startswith("ORDERS_CANCEL_L0_CONFIRM:"):
        try:
            _, sym, amount_str = data.split(":", 2)
        except ValueError:
            return {"ok": True}
        symbol = (sym or "").upper().strip()
        if not symbol:
            return {"ok": True}
        try:
            amount = int(amount_str)
        except Exception:
            amount = 0
        msg, kb = confirm_cancel_l0(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb or None)
        return {"ok": True}

    # ORDERS → CANCEL → подтверждение LIMIT 1
    if data.startswith("ORDERS_CANCEL_L1_CONFIRM:"):
        try:
            _, sym, amount_str = data.split(":", 2)
        except ValueError:
            return {"ok": True}
        symbol = (sym or "").upper().strip()
        if not symbol:
            return {"ok": True}
        try:
            amount = int(amount_str)
        except Exception:
            amount = 0
        msg, kb = confirm_cancel_l1(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb or None)
        return {"ok": True}

    # ORDERS → CANCEL → подтверждение LIMIT 2
    if data.startswith("ORDERS_CANCEL_L2_CONFIRM:"):
        try:
            _, sym, amount_str = data.split(":", 2)
        except ValueError:
            return {"ok": True}
        symbol = (sym or "").upper().strip()
        if not symbol:
            return {"ok": True}
        try:
            amount = int(amount_str)
        except Exception:
            amount = 0
        msg, kb = confirm_cancel_l2(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb or None)
        return {"ok": True}

    # ORDERS → CANCEL → подтверждение LIMIT 3
    if data.startswith("ORDERS_CANCEL_L3_CONFIRM:"):
        try:
            _, sym, amount_str = data.split(":", 2)
        except ValueError:
            return {"ok": True}
        symbol = (sym or "").upper().strip()
        if not symbol:
            return {"ok": True}
        try:
            amount = int(amount_str)
        except Exception:
            amount = 0
        msg, kb = confirm_cancel_l3(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb or None)
        return {"ok": True}

    
    # ORDERS → FILL → выбор уровня для пометки исполненным
    if data.startswith("ORDERS_FILL:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_FILL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_FILL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_FILL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_FILL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_FILL_L3:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        await _edit_markup(kb)
        return {"ok": True}

    # FILL OCO/L0-L3 → показать подтверждение
    if data.startswith("ORDERS_FILL_OCO:"):
        _, sym_raw = data.split(":", 1)
        symbol = (sym_raw or "").upper().strip()
        msg, kb = prepare_fill_oco(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb)
        return {"ok": True}

    if data.startswith("ORDERS_FILL_L0:"):
        _, sym_raw = data.split(":", 1)
        symbol = (sym_raw or "").upper().strip()
        msg, kb = prepare_fill_l0(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb)
        return {"ok": True}

    if data.startswith("ORDERS_FILL_L1:"):
        _, sym_raw = data.split(":", 1)
        symbol = (sym_raw or "").upper().strip()
        msg, kb = prepare_fill_l1(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb)
        return {"ok": True}

    if data.startswith("ORDERS_FILL_L2:"):
        _, sym_raw = data.split(":", 1)
        symbol = (sym_raw or "").upper().strip()
        msg, kb = prepare_fill_l2(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb)
        return {"ok": True}

    if data.startswith("ORDERS_FILL_L3:"):
        _, sym_raw = data.split(":", 1)
        symbol = (sym_raw or "").upper().strip()
        msg, kb = prepare_fill_l3(symbol)
        await tg_send(chat_id, _code(msg), reply_markup=kb)
        return {"ok": True}

    # FILL CONFIRM callbacks
    if data.startswith("ORDERS_FILL_OCO_CONFIRM:"):
        _, tail = data.split(":", 1)
        try:
            sym_raw, amt_raw = tail.split(":", 1)
            amount = int(amt_raw)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        msg, kb = confirm_fill_oco(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb)
        return {"ok": True}

    if data.startswith("ORDERS_FILL_L0_CONFIRM:"):
        _, tail = data.split(":", 1)
        try:
            sym_raw, amt_raw = tail.split(":", 1)
            amount = int(amt_raw)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        msg, kb = confirm_fill_l0(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb)
        return {"ok": True}

    if data.startswith("ORDERS_FILL_L1_CONFIRM:"):
        _, tail = data.split(":", 1)
        try:
            sym_raw, amt_raw = tail.split(":", 1)
            amount = int(amt_raw)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        msg, kb = confirm_fill_l1(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb)
        return {"ok": True}

    if data.startswith("ORDERS_FILL_L2_CONFIRM:"):
        _, tail = data.split(":", 1)
        try:
            sym_raw, amt_raw = tail.split(":", 1)
            amount = int(amt_raw)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        msg, kb = confirm_fill_l2(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb)
        return {"ok": True}

    if data.startswith("ORDERS_FILL_L3_CONFIRM:"):
        _, tail = data.split(":", 1)
        try:
            sym_raw, amt_raw = tail.split(":", 1)
            amount = int(amt_raw)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        msg, kb = confirm_fill_l3(symbol, amount)
        await tg_send(chat_id, _code(msg), reply_markup=kb)
        return {"ok": True}

# ORDERS back from submenu to root BUDGET/ORDERS row
    if data.startswith("ORDERS_BACK_ROOT:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        kb = {
            "inline_keyboard": [
                [
                    {"text": "BUDGET", "callback_data": f"BUDGET:{symbol}"},
                    {"text": "ORDERS", "callback_data": f"ORDERS:{symbol}"},
                ]
            ]
        }
        await _edit_markup(kb)
        return {"ok": True}

    # ORDERS back from OPEN submenu to ORDERS menu
    if data.startswith("ORDERS_BACK_MENU:"):
        try:
            _, sym_raw = data.split(":", 1)
        except ValueError:
            return {"ok": True}
        symbol = (sym_raw or "").upper().strip()
        if not symbol:
            return {"ok": True}
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OPEN", "callback_data": f"ORDERS_OPEN:{symbol}"},
                    {"text": "CANCEL", "callback_data": f"ORDERS_CANCEL:{symbol}"},
                    {"text": "FILL", "callback_data": f"ORDERS_FILL:{symbol}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_ROOT:{symbol}"},
                ],
            ]
        }
        await _edit_markup(kb)
        return {"ok": True}

    return {"ok": True}



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


@app.head("/health")
async def health_head():
    return {"ok": True}


@app.get("/")
async def root():
    return {"ok": True, "service": "traider-bot"}


@app.head("/")
async def root_head():
    return {"ok": True}


@app.post("/telegram")
async def telegram_webhook(update: Request):
    try:
        data = await update.json()
    except Exception:
        data = {}

    # inline keyboard callbacks
    callback = data.get("callback_query")
    if callback:
        return await _answer_callback(callback)

    message = data.get("message") or data.get("edited_message") or {}
    text = (message.get("text") or message.get("caption") or "").strip()

    # Стикер → команда
    if not text and message.get("sticker"):
        st = message["sticker"]
        text = (
            STICKER_TO_COMMAND.get(st.get("file_unique_id"))
            or STICKER_TO_COMMAND.get(st.get("file_id"))
            or ""
        ).strip()

    text_norm = text
    text_lower = text_norm.lower()
    text_upper = text_norm.upper()
    chat_id = str((message.get("chat") or {}).get("id") or "")
    if not chat_id:
        return {"ok": True}

    # Budget input mode: if this chat is expected to send a budget value
    pending = get_budget_input(chat_id)
    if pending and not text_lower.startswith("/"):
        raw = (text or "").strip()
        try:
            # Только целые числа >= 0
            val = int(raw)
            if val < 0:
                raise ValueError()
        except Exception:
            msg = f"{pending['symbol']}\nНужно ввести целое число ≥ 0 в USDC. Попробуй ещё раз:"
            await tg_send(chat_id, _code(msg))
            return {"ok": True}
        info = set_pair_budget(pending["symbol"], pending["month"], val)
        clear_budget_input(chat_id)
        # После установки бюджета сразу отправляем карточку по символу
        try:
            sym = info.get("symbol") or pending["symbol"]
            card = build_symbol_message(sym)
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "BUDGET", "callback_data": f"BUDGET:{sym}"},
                        {"text": "ORDERS", "callback_data": f"ORDERS:{sym}"},
                    ]
                ]
            }
            await tg_send(chat_id, _code(card), reply_markup=kb)
        except Exception:
            # если что-то пошло не так, просто молча выходим
            pass
        return {"ok": True}

    # /invested <delta>  |  /invest <delta>
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

    # /coins [SYMBOLS...] (только показать/валидировать; без сохранения)
    if text_lower.startswith("/coins"):
        parts = text.split(maxsplit=1)
        # No arguments -> read pairs.json and show status
        if len(parts) == 1:
            pairs = load_pairs()
            if pairs:
                reply = "Активные пары: " + ", ".join(pairs)
            else:
                reply = "Активных пар нет. Добавьте пары командой /coins BTCUSDC ETHUSDC ..."
            await tg_send(chat_id, _code(reply))
            return {"ok": True}

        # With arguments -> parse, validate, dedupe, write pairs.json
        rest = parts[1].strip()
        items = [x.strip().upper() for x in rest.split() if x.strip()]
        # Validate ^[A-Z]+USDC$
        valids = [s for s in items if re.fullmatch(r"^[A-Z]+USDC$", s)]
        if not valids:
            await tg_send(chat_id, _code("Не удалось найти ни одного корректного тикера (формат: XXXUSDC)."))
            return {"ok": True}

        # Deduplicate preserving first occurrence, then sort A→Z (allowed by спецификация)
        seen = set()
        deduped = []
        for s in valids:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        deduped_sorted = sorted(deduped)

        # Save as {"pairs":[...]}
        try:
            save_pairs_json(deduped_sorted)
            reply = "Пары обновлены: " + ", ".join(deduped_sorted)
        except Exception as e:
            reply = f"Ошибка записи pairs.json: {e.__class__.__name__}"
        await tg_send(chat_id, _code(reply))
        return {"ok": True}


    # /now [<SYMBOL>|long|short]
    if text_lower.startswith("/now"):
        parts = text.strip().split()
        symbol_arg = None
        if len(parts) >= 2 and parts[1].lower() not in ("long", "short"):
            symbol_arg = parts[1].upper()

        parts = (text or "").strip().split()
        mode_arg = None
        if len(parts) >= 2 and parts[1].strip().lower() in ("long", "short"):
            mode_arg = parts[1].strip().upper()

        count, msg = await run_now(symbol_arg)
        _log("/now result:", count)

        # Если указан символ — одна карточка с кнопкой BUDGET
        if symbol_arg:
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "BUDGET", "callback_data": f"BUDGET:{symbol_arg.upper()}"},
                        {"text": "ORDERS", "callback_data": f"ORDERS:{symbol_arg.upper()}"},
                    ]
                ]
            }
            await tg_send(chat_id, _code(msg), reply_markup=kb)
            return {"ok": True}

        # Иначе: summary + по каждой паре
        await tg_send(chat_id, _code(msg))

        try:
            pairs = load_pairs()
        except Exception:
            pairs = []

        # Фильтр по LONG/SHORT если задан
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
                kb = {
                    "inline_keyboard": [
                        [
                            {"text": "BUDGET", "callback_data": f"BUDGET:{sym}"},
                            {"text": "ORDERS", "callback_data": f"ORDERS:{sym}"},
                        ]
                    ]
                }
                await tg_send(chat_id, _code(smsg), reply_markup=kb)
            except Exception:
                pass
        return {"ok": True}

    # /mode
    if text_lower.startswith("/mode"):
        parts = text.split()
        if len(parts) == 1:
            summary = list_modes()
            await tg_send(chat_id, _code(f"Режимы: {summary}"))
            return {"ok": True}
        if len(parts) == 2:
            sym, md = get_mode(parts[1])
            if not sym:
                await tg_send(chat_id, _code("Некорректная команда"))
                return {"ok": True}
            await tg_send(chat_id, _code(f"{sym}: {md}"))
            return {"ok": True}
        if len(parts) >= 3:
            sym = parts[1]
            md = parts[2]
            try:
                sym, md = set_mode(sym, md)
                await tg_send(chat_id, _code(f"{sym} → {md}"))
            except ValueError:
                await tg_send(chat_id, _code("Некорректный режим"))
            return {"ok": True}

    # Шорткаты вида /BTCUSDC /ETHUSDC ...
    if text_lower.startswith("/") and len(text_norm) > 2:
        sym = text_upper[1:].split()[0].upper()
        if sym not in ("NOW", "MODE", "PORTFOLIO", "COINS", "DATA", "JSON", "INVESTED", "INVEST", "MARKET", "SCHEDULER"):
            msg = build_symbol_message(sym)
            await tg_send(chat_id, _code(msg))
            return {"ok": True}

    # /market [SYMBOL]
    if text_lower.startswith("/market"):
        parts = text.split()
        if len(parts) == 1:
            pairs = load_pairs()
            if not pairs:
                await tg_send(chat_id, _code("Пары: —"))
                return {"ok": True}
            lines = [_market_line_for(sym) for sym in pairs]
            await tg_send(chat_id, _code("\n".join(lines)))
            return {"ok": True}
        sym = parts[1].strip().upper()
        await tg_send(chat_id, _code(_market_line_for(sym)))
        return {"ok": True}

    # /data ...
    if text_lower.startswith("/data"):
        parts = text.split()
        if len(parts) == 1:
            files = sorted(
                [os.path.basename(p) for p in glob.glob(os.path.join(STORAGE_DIR, "*")) if os.path.isfile(p)]
            )
            msg = "Файлы: " + (", ".join(files) if files else "—")
            await tg_send(chat_id, _code(msg))
            return {"ok": True}

        if len(parts) >= 3 and parts[1].strip().lower() == "delete":
            name = os.path.basename(parts[2].strip())
            files = sorted(
                [os.path.basename(p) for p in glob.glob(os.path.join(STORAGE_DIR, "*")) if os.path.isfile(p)]
            )
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

        name = os.path.basename(parts[1].strip())
        path = os.path.join(STORAGE_DIR, name)
        if not (os.path.exists(path) and os.path.isfile(path)):
            await tg_send(chat_id, _code("Файл не найден"))
            return {"ok": True}
        await tg_send_file(chat_id, path, filename=name, caption=name)
        return {"ok": True}

    # /scheduler ...
    if text_lower.startswith("/scheduler"):
        parts = (text or "").strip().split()
        if len(parts) >= 2 and parts[1].lower() == "config":
            st = scheduler_get_state()
            await tg_send(chat_id, _code(json.dumps(st, ensure_ascii=False, indent=2)))
            return {"ok": True}

        if len(parts) >= 2 and parts[1].lower() in ("on", "off"):
            on = parts[1].lower() == "on"
            scheduler_set_enabled(on)
            if on:
                await start_collector()
            else:
                await stop_collector()
            await tg_send(chat_id, _code(f"Scheduler: {'ON' if on else 'OFF'}"))
            return {"ok": True}

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

        if len(parts) >= 2 and parts[1].isdigit():
            interval = int(parts[1])
            jitter = None
            if len(parts) >= 3 and parts[2].isdigit():
                jitter = int(parts[2])
            interval = max(15, min(43200, interval))
            if jitter is not None:
                jitter = max(1, min(5, jitter))
            st = scheduler_set_timing(interval, jitter)
            await tg_send(chat_id, _code("OK"))
            if st.get("enabled"):
                await stop_collector()
                await start_collector()
            return {"ok": True}

        await tg_send(chat_id, _code("Команды: /scheduler on|off | config | <sec> [jitter] | tail <N>"))
        return {"ok": True}

    # /portfolio
    if text_lower.startswith("/portfolio"):
        try:
            reply = await build_portfolio_message(client, BINANCE_API_KEY, BINANCE_API_SECRET, STORAGE_DIR)
            _log("/portfolio built", "len=", len(reply or ""), "head=", (reply or "").splitlines()[0][:160])
        except Exception as e:
            reply = f"Ошибка портфеля: {e}"
        await tg_send(chat_id, reply or "Нет данных.")
        return {"ok": True}

    return {"ok": True}


def _market_line_for(symbol: str) -> str:
    path = os.path.join(STORAGE_DIR, f"{symbol}.json")
    data = _load_json_safe(path)
    trade_mode = str((data.get("trade_mode") or "SHORT")).upper()
    market_mode = str((data.get("market_mode") or "RANGE")).upper()
    mm_emoji = {"UP": "⬆️", "DOWN": "⬇️", "RANGE": "🔄"}.get(market_mode, "🔄")
    tm_emoji = {"LONG": "📈", "SHORT": "📉"}.get(trade_mode, "")
    return f"{symbol} {market_mode}{mm_emoji} Mode {trade_mode}{tm_emoji}"


# --- Telegram-compatible alias: /webhook/<token> ---
@app.post("/webhook/{token}")
async def telegram_webhook_alias(token: str, update: Request):
    expected = os.getenv("TRAIDER_BOT_TOKEN") or ""
    if expected and token != expected:
        # тихо подтверждаем, чтобы TG не долбил ретраями
        return {"ok": True, "description": "token mismatch"}
    return await telegram_webhook(update)


# === Metrics lifecycle ===
@app.on_event("startup")
async def _startup_metrics():
    await start_collector()


@app.on_event("shutdown")
async def _shutdown_metrics():
    await stop_collector()