from __future__ import annotations
from datetime import datetime
from typing import Tuple, Dict, Any

import os, json

from budget import get_pair_budget, get_pair_levels, save_pair_levels, recompute_pair_aggregates
from symbol_info import build_symbol_message

# локальные константы распределения недельного бюджета — копия из app.py
WEEKLY_PERCENT = {
    "UP":   {"OCO": 10, "L0": 10, "L1": 5,  "L2": 0,  "L3": 0},
    "RANGE":{"OCO": 5,  "L0": 5,  "L1": 10, "L2": 5,  "L3": 0},
    "DOWN": {"OCO": 0,  "L0": 0,  "L1": 10, "L2": 10, "L3": 5},
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

def _mode_key_from_symbol(symbol: str) -> str:
    sdata = _load_symbol_data(symbol)
    market_mode = sdata.get("market_mode")
    raw_mode = market_mode.get("12h") if isinstance(market_mode, dict) else market_mode
    raw_mode_str = str(raw_mode or "").upper()
    if "UP" in raw_mode_str:
        return "UP"
    elif "DOWN" in raw_mode_str:
        return "DOWN"
    return "RANGE"

def _flag_desc(flag: str) -> str:
    if flag == "🟢":
        return "цена ниже / внизу коридора — можно брать по рынку"
    if flag == "🟡":
        return "можно открыть по рекомендациям"
    if flag == "🔴":
        return "цена высока — ордер ставить рискованно"
    return "нет автофлага"

def prepare_open_oco(symbol: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовить текст и клавиатуру подтверждения 'OCO OPEN'.
    Возвращает (message_text, reply_markup). Исключения не бросает, сообщения об ошибке возвращаются как текст.
    """
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)

    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен (Wk{week}) или бюджет 0 — OCO недоступен.", {}

    mode_key = _mode_key_from_symbol(symbol)
    perc = WEEKLY_PERCENT.get(mode_key, WEEKLY_PERCENT["RANGE"])
    p_oco = int(perc.get("OCO") or 0)
    if p_oco <= 0:
        return f"{symbol} {month}\nДля уровня OCO в режиме {mode_key} доля бюджета 0% — OCO не используется.", {}

    quota = int(round(budget * p_oco / 100.0))
    levels = get_pair_levels(symbol, month) or {}
    lvl_state = levels.get("OCO") or {}
    used = int(lvl_state.get("reserved") or 0) + int(lvl_state.get("spent") or 0)
    available = quota - used
    if available <= 0:
        return f"{symbol} {month}\nЛимит по OCO уже исчерпан (доступно 0 USDC).", {}
    if free <= 0:
        return f"{symbol} {month}\nСвободный бюджет 0 USDC — сначала освободите бюджет.", {}

    if available > free:
        # предупреждение о нехватке свободного бюджета
        return (
            f"{symbol} {month}\n"
            f"По уровню OCO доступно {available} USDC, но свободно в бюджете только {free} USDC.\n"
            f"Сначала освободите бюджет или уменьшите другие уровни.",
            {}
        )

    # автофлаг и описание
    sdata = _load_symbol_data(symbol)
    flags = sdata.get("flags") or {}
    flag_oco = flags.get("OCO") or ""
    flag_desc = _flag_desc(flag_oco)

    mon_disp = month
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"

    msg = (
        f"{symbol} {mon_disp} Wk{week}\n"
        f"OCO OPEN\n\n"
        f"Сумма: {available} USDC\n"
        f"Флаг: {flag_oco or '-'} ({flag_desc})\n"
        f"Поставить виртуальный OCO-ордер на {available} USDC?"
    )
    kb = {
        "inline_keyboard": [
            [
                {"text": "CONFIRM", "callback_data": f"ORDERS_OPEN_OCO_CONFIRM:{symbol}:{available}"},
                {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
            ]
        ]
    }
    return msg, kb

def confirm_open_oco(symbol: str, amount: int) -> Tuple[str, Dict[str, Any]]:
    """Подтвердить OCO OPEN: обновляет резерв и возвращает текст/клавиатуру для карточки символа.
    Возвращает (text, reply_markup). Если карточку собрать не удалось — вернётся краткое текстовое подтверждение.
    """
    symbol = (symbol or "").upper().strip()
    if not symbol or amount <= 0:
        return "Некорректные параметры операции.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)

    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл не запущен или бюджет 0 — операция отклонена.", {}

    # пересчёт лимитов на момент подтверждения
    mode_key = _mode_key_from_symbol(symbol)
    perc = WEEKLY_PERCENT.get(mode_key, WEEKLY_PERCENT["RANGE"])
    p_oco = int(perc.get("OCO") or 0)
    if p_oco <= 0:
        return f"{symbol} {month}\nДля уровня OCO в режиме {mode_key} доля бюджета 0% — операция отменена.", {}

    quota = int(round(budget * p_oco / 100.0))
    levels = get_pair_levels(symbol, month) or {}
    lvl_state = levels.get("OCO") or {}
    used = int(lvl_state.get("reserved") or 0) + int(lvl_state.get("spent") or 0)
    available = quota - used
    if available <= 0 or free <= 0:
        return f"{symbol} {month}\nЛимит по OCO или свободный бюджет уже исчерпаны — операция отменена.", {}

    actual = min(int(amount), available, free)
    if actual <= 0:
        return f"{symbol} {month}\nФактическая доступная сумма 0 USDC — операция отменена.", {}

    # обновляем состояние уровня OCO
    new_reserved = int(lvl_state.get("reserved") or 0) + actual
    levels["OCO"] = {
        "reserved": new_reserved,
        "spent": int(lvl_state.get("spent") or 0),
    }
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    # пробуем собрать карточку символа
    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard": [[
                {"text": "BUDGET", "callback_data": f"BUDGET:{sym}"},
                {"text": "ORDERS", "callback_data": f"ORDERS:{sym}"},
            ]]
        }
        return card, kb
    except Exception:
        # fallback короткий текст
        msg = (
            f"{symbol} {month}\n"
            f"OCO: виртуальный ордер на {actual} USDC учтён в резерве.\n"
            f"Бюджет: {info2.get('budget')} | "
            f"⏳ {info2.get('reserve')} | "
            f"💸 {info2.get('spent')} | "
            f"🎯 {info2.get('free')}"
        )
        return msg, {}
