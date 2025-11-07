from __future__ import annotations
from datetime import datetime
from typing import Tuple, Dict, Any
import os, json

from budget import get_pair_budget, get_pair_levels, save_pair_levels, recompute_pair_aggregates
from auto_flags import compute_all_flags
from symbol_info import build_symbol_message

# Недельные доли по режиму рынка
WEEKLY_PERCENT = {
    "UP":   {"OCO": 10, "L0": 10, "L1": 5,  "L2": 0,  "L3": 0},
    "RANGE":{"OCO": 5,  "L0": 5,  "L1": 10, "L2": 5,  "L3": 0},
    "DOWN": {"OCO": 5,  "L0": 0,  "L1": 5, "L2": 10, "L3": 5},
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


def _save_symbol_data(symbol: str, data: dict) -> None:
    """Безопасная запись JSON по монете (best-effort)."""
    path = _symbol_data_path(symbol)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        # best-effort: не ломаем бот из-за ошибок диска
        pass


def _recompute_symbol_flags(symbol: str) -> None:
    """Пересчитать автофлаги (включая ⚠️/✅) после изменения budget-levels.

    Используется после OPEN/CANCEL/FILL, чтобы карточка сразу показывала
    актуальные флаги, не ждя следующего прохода metrics_runner.
    """
    try:
        sdata = _load_symbol_data(symbol)
        if not isinstance(sdata, dict):
            return
        # trade_mode нужен, чтобы понять, что монета вообще торгуется
        mode = str(sdata.get("trade_mode") or "").upper()
        if mode != "LONG":
            # пока флаги считаем только для LONG-карточек
            pass
        sdata["flags"] = compute_all_flags(sdata)
        _save_symbol_data(symbol, sdata)
    except Exception:
        # не критично, просто не обновим флаги немедленно
        pass


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

def _prepare_open_level(symbol: str, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)

    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен (Wk{week}) или бюджет 0 — {title} недоступен.", {}

    mode_key = _mode_key_from_symbol(symbol)
    perc = WEEKLY_PERCENT.get(mode_key, WEEKLY_PERCENT["RANGE"])
    p = int(perc.get(lvl) or 0)
    if p <= 0:
        return f"{symbol} {month}\nДля уровня {title} в режиме {mode_key} доля бюджета 0% — {title} не используется.", {}

    quota = int(round(budget * p / 100.0))
    levels = get_pair_levels(symbol, month) or {}
    lvl_state = levels.get(lvl) or {}
    used = int(lvl_state.get("reserved") or 0) + int(lvl_state.get("spent") or 0)
    available = quota - used
    if available <= 0:
        return f"{symbol} {month}\nЛимит по {title} уже исчерпан (доступно 0 USDC).", {}
    if free <= 0:
        return f"{symbol} {month}\nСвободный бюджет 0 USDC — сначала освободите бюджет.", {}

    if available > free:
        return (
            f"{symbol} {month}\n"
            f"По уровню {title} доступно {available} USDC, но свободно в бюджете только {free} USDC.\n"
            f"Сначала освободите бюджет или уменьшите другие уровни.",
            {}
        )

    sdata = _load_symbol_data(symbol)
    flags = sdata.get("flags") or {}
    flag_val = flags.get(lvl) or ""
    flag_desc = _flag_desc(flag_val)

    mon_disp = month
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"

    msg = (
        f"{symbol} {mon_disp} Wk{week}\n"
        f"{title} OPEN\n\n"
        f"Сумма: {available} USDC\n"
        f"Флаг: {flag_val or '-'} ({flag_desc})\n"
        f"Поставить виртуальный {title} на {available} USDC?"
    )
    cb = f"ORDERS_OPEN_{lvl}_CONFIRM"
    kb = {
        "inline_keyboard": [[
            {"text": "CONFIRM", "callback_data": f"{cb}:{symbol}:{available}"},
            {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
        ]]
    }
    return msg, kb

def _confirm_open_level(symbol: str, amount: int, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    symbol = (symbol or "").upper().strip()
    if not symbol or int(amount) <= 0:
        return "Некорректные параметры операции.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)

    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл не запущен или бюджет 0 — операция отменена.", {}

    mode_key = _mode_key_from_symbol(symbol)
    perc = WEEKLY_PERCENT.get(mode_key, WEEKLY_PERCENT["RANGE"])
    p = int(perc.get(lvl) or 0)
    if p <= 0:
        return f"{symbol} {month}\nДля уровня {title} в режиме {mode_key} доля бюджета 0% — операция отменена.", {}

    quota = int(round(budget * p / 100.0))
    levels = get_pair_levels(symbol, month) or {}
    lvl_state = levels.get(lvl) or {}
    used = int(lvl_state.get("reserved") or 0) + int(lvl_state.get("spent") or 0)
    available = quota - used
    if available <= 0 or free <= 0:
        return f"{symbol} {month}\nЛимит по {title} или свободный бюджет уже исчерпаны — операция отменена.", {}

    actual = min(int(amount), available, free)
    if actual <= 0:
        return f"{symbol} {month}\nФактическая доступная сумма 0 USDC — операция отменена.", {}

    new_reserved = int(lvl_state.get("reserved") or 0) + actual
    levels[lvl] = {"reserved": new_reserved, "spent": int(lvl_state.get("spent") or 0)}
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    # После изменения резервов обновляем автофлаги (включая ⚠️/✅).
    _recompute_symbol_flags(symbol)

    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {"inline_keyboard": [[
            {"text": "BUDGET", "callback_data": f"BUDGET:{sym}"},
            {"text": "ORDERS", "callback_data": f"ORDERS:{sym}"},
        ]]}
        return card, kb
    except Exception:
        msg = (
            f"{symbol} {month}\n"
            f"{title}: виртуальный ордер на {actual} USDC учтён в резерве.\n"
            f"Бюджет: {info2.get('budget')} | "
            f"⏳ {info2.get('reserve')} | "
            f"💸 {info2.get('spent')} | "
            f"🎯 {info2.get('free')}"
        )
        return msg, {}

def _prepare_cancel_level(symbol: str, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка отмены виртуального ордера: показ суммы в резерве и подтверждение."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    week = int(info.get("week") or 0)

    levels = get_pair_levels(symbol, month)
    lvl_state = levels.get(lvl) or {}
    reserved = int(lvl_state.get("reserved") or 0)

    mon_disp = month
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"

    if reserved <= 0:
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} CANCEL\n\n"
            f"Нет виртуального ордера на уровне {title} (в резерве 0 USDC)."
        )
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
        return msg, kb

    msg = (
        f"{symbol} {mon_disp} Wk{week}\n"
        f"{title} CANCEL\n\n"
        f"Сейчас в резерве: {reserved} USDC\n"
        f"Вернуть в free:   {reserved} USDC\n\n"
        f"Отменить виртуальный {title} на {reserved} USDC?"
    )
    cb = f"ORDERS_CANCEL_{lvl}_CONFIRM"
    kb = {
        "inline_keyboard": [[
            {"text": "CONFIRM", "callback_data": f"{cb}:{symbol}:{reserved}"},
            {"text": "↩️", "callback_data": f"ORDERS_CANCEL:{symbol}"},
        ]]
    }
    return msg, kb


def _confirm_cancel_level(symbol: str, amount: int, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подтверждение отмены: возвращаем резерв в free."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректные параметры операции.", {}

    month = datetime.now().strftime("%Y-%m")
    levels = get_pair_levels(symbol, month)
    lvl_state = levels.get(lvl) or {}
    reserved = int(lvl_state.get("reserved") or 0)

    if reserved <= 0:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        msg = (
            f"{symbol} {mon_disp} Wk?\n"
            f"{title} CANCEL\n\n"
            f"Нечего отменять: резерв уже 0 USDC."
        )
        sym = symbol
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{sym}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{sym}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{sym}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{sym}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{sym}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return msg, kb

    try:
        requested = int(amount)
    except Exception:
        requested = 0
    if requested <= 0:
        requested = reserved
    actual = min(reserved, requested)
    new_reserved = reserved - actual
    if new_reserved < 0:
        new_reserved = 0

    levels[lvl] = {
        "reserved": new_reserved,
        "spent": int(lvl_state.get("spent") or 0),
    }
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    # После изменения резервов обновляем автофлаги (⚠️/✅/авто).
    _recompute_symbol_flags(symbol)

    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard": [
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{sym}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{sym}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{sym}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{sym}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{sym}"},
                ],
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        msg = (
            f"{symbol} {mon_disp}\n"
            f"{title}: отменён виртуальный ордер на {actual} USDC.\n"
            f"Бюджет: {info2.get('budget')} | "
            f"⏳ {info2.get('reserve')} | "
            f"💸 {info2.get('spent')} | "
            f"🎯 {info2.get('free')}"
        )
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
        return msg, kb


# Публичные API для уровней

# Публичные API для уровней
def prepare_open_oco(symbol: str):  return _prepare_open_level(symbol, "OCO", "OCO")
def confirm_open_oco(symbol: str, amount: int):  return _confirm_open_level(symbol, amount, "OCO", "OCO")

def prepare_open_l0(symbol: str):   return _prepare_open_level(symbol, "L0", "LIMIT 0")
def confirm_open_l0(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L0", "LIMIT 0")

def prepare_open_l1(symbol: str):   return _prepare_open_level(symbol, "L1", "LIMIT 1")
def confirm_open_l1(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L1", "LIMIT 1")

def prepare_open_l2(symbol: str):   return _prepare_open_level(symbol, "L2", "LIMIT 2")
def confirm_open_l2(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L2", "LIMIT 2")

def prepare_open_l3(symbol: str):   return _prepare_open_level(symbol, "L3", "LIMIT 3")
def confirm_open_l3(symbol: str, amount: int):   return _confirm_open_level(symbol, amount, "L3", "LIMIT 3")

def prepare_cancel_oco(symbol: str):  return _prepare_cancel_level(symbol, "OCO", "OCO")
def confirm_cancel_oco(symbol: str, amount: int):  return _confirm_cancel_level(symbol, amount, "OCO", "OCO")

def prepare_cancel_l0(symbol: str):   return _prepare_cancel_level(symbol, "L0", "LIMIT 0")
def confirm_cancel_l0(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L0", "LIMIT 0")

def prepare_cancel_l1(symbol: str):   return _prepare_cancel_level(symbol, "L1", "LIMIT 1")
def confirm_cancel_l1(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L1", "LIMIT 1")

def prepare_cancel_l2(symbol: str):   return _prepare_cancel_level(symbol, "L2", "LIMIT 2")
def confirm_cancel_l2(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L2", "LIMIT 2")

def prepare_cancel_l3(symbol: str):   return _prepare_cancel_level(symbol, "L3", "LIMIT 3")
def confirm_cancel_l3(symbol: str, amount: int):   return _confirm_cancel_level(symbol, amount, "L3", "LIMIT 3")
