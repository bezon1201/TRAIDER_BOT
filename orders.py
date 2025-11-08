from __future__ import annotations
from datetime import datetime
from typing import Tuple, Dict, Any
import os, json

from budget import get_pair_budget, get_pair_levels, save_pair_levels, recompute_pair_aggregates, set_pair_week
from auto_flags import compute_all_flags
from symbol_info import build_symbol_message

# Недельные доли по режиму рынка
WEEKLY_PERCENT = {
    "UP":   {"OCO": 10, "L0": 10, "L1": 5,  "L2": 0,  "L3": 0},
    "RANGE":{"OCO": 5,  "L0": 5,  "L1": 10, "L2": 5,  "L3": 0},
    "DOWN": {"OCO": 5,  "L0": 0,  "L1": 5, "L2": 10, "L3": 5},
}

LEVEL_KEYS = ("OCO", "L0", "L1", "L2", "L3")


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



def _compute_base_quota(symbol: str, month: str, lvl: str, budget: int) -> int:
    """Рассчитать базовую квоту по уровню на основе режима рынка и месячного бюджета."""
    if budget <= 0:
        return 0
    mode_key = _mode_key_from_symbol(symbol)
    perc = WEEKLY_PERCENT.get(mode_key, WEEKLY_PERCENT["RANGE"])
    try:
        p = int(perc.get(lvl) or 0)
    except Exception:
        p = 0
    if p <= 0:
        return 0
    quota = int(round(budget * p / 100.0))
    if quota < 0:
        quota = 0
    return quota


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

    # базовая квота по режиму рынка
    base_quota = _compute_base_quota(symbol, month, lvl, budget)
    if base_quota <= 0:
        mode_key = _mode_key_from_symbol(symbol)
        return (
            f"{symbol} {month}\n"
            f"Для уровня {title} в режиме {mode_key} доля бюджета 0% — {title} не используется.",
            {}
        )

    levels = get_pair_levels(symbol, month) or {}
    lvl_state = levels.get(lvl) or {}
    try:
        week_quota = int(lvl_state.get("week_quota") or 0)
    except Exception:
        week_quota = 0

    # если квота на неделю ещё не установлена (старые данные) — берём базовую
    quota = week_quota if week_quota > 0 else base_quota

    reserved = int(lvl_state.get("reserved") or 0)
    spent = int(lvl_state.get("spent") or 0)
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1
    used = reserved + (spent if last_fill_week == week else 0)
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
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    flag_val = flags.get(lvl) or "-"
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

    base_quota = _compute_base_quota(symbol, month, lvl, budget)
    if base_quota <= 0:
        mode_key = _mode_key_from_symbol(symbol)
        return (
            f"{symbol} {month}\n"
            f"Для уровня {title} в режиме {mode_key} доля бюджета 0% — операция отменена.",
            {}
        )

    levels = get_pair_levels(symbol, month) or {}
    lvl_state = levels.get(lvl) or {}
    try:
        week_quota = int(lvl_state.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    quota = week_quota if week_quota > 0 else base_quota

    reserved = int(lvl_state.get("reserved") or 0)
    spent = int(lvl_state.get("spent") or 0)
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1
    used = reserved + (spent if last_fill_week == week else 0)
    available = quota - used
    if available <= 0 or free <= 0:
        return f"{symbol} {month}\nЛимит по {title} или свободный бюджет уже исчерпаны — операция отменена.", {}

    actual = min(int(amount), available, free)
    if actual <= 0:
        return f"{symbol} {month}\nФактическая доступная сумма 0 USDC — операция отменена.", {}

    new_reserved = int(lvl_state.get("reserved") or 0) + actual
    new_spent = int(lvl_state.get("spent") or 0)
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1

    levels[lvl] = {
        "reserved": new_reserved,
        "spent": new_spent,
        "week_quota": week_quota if week_quota > 0 else quota,
        "last_fill_week": last_fill_week,
    }
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    
    # увеличиваем номер недели
    new_week = week + 1
    info3 = get_pair_budget(symbol, month)
# После изменения резервов обновляем автофлаги (включая ⚠️/✅).
    _recompute_symbol_flags(symbol)

    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {"inline_keyboard": [
            [
                {"text": "OCO", "callback_data": f"ORDERS_OPEN_OCO:{sym}"},
                {"text": "LIMIT 0", "callback_data": f"ORDERS_OPEN_L0:{sym}"},
                {"text": "LIMIT 1", "callback_data": f"ORDERS_OPEN_L1:{sym}"},
                {"text": "LIMIT 2", "callback_data": f"ORDERS_OPEN_L2:{sym}"},
                {"text": "LIMIT 3", "callback_data": f"ORDERS_OPEN_L3:{sym}"},
            ],
            [
                {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
            ],
        ]}
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
        return msg, kb

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
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
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

    # сохраняем только резерв, остальные поля (spent/week_quota/last_fill_week) не трогаем
    try:
        spent = int(lvl_state.get("spent") or 0)
    except Exception:
        spent = 0
    try:
        week_quota = int(lvl_state.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1

    levels[lvl] = {
        "reserved": new_reserved,
        "spent": spent,
        "week_quota": week_quota,
        "last_fill_week": last_fill_week,
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
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
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


def recompute_flags_for_symbol(symbol: str) -> None:
    """Публичный помощник для пересчёта флагов по монете."""
    _recompute_symbol_flags(symbol)


def _prepare_fill_level(symbol: str, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка пометки уровня как исполненного (FILL)."""
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

    if week <= 0:
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} FILL\n\n"
            f"Цикл ещё не запущен — пометка исполнения недоступна."
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{symbol}"},
                ],
            ]
        }
        return msg, kb

    if reserved <= 0:
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} FILL\n\n"
            f"Нет открытого виртуального ордера на уровне {title} (в резерве 0 USDC)."
        )
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
        return msg, kb

    msg = (
        f"{symbol} {mon_disp} Wk{week}\n"
        f"{title} FILL\n\n"
        f"Сейчас в резерве: {reserved} USDC\n"
        f"Перевести в spent: {reserved} USDC?\n\n"
        f"Пометить виртуальный {title} как полностью исполненный?"
    )
    cb = f"ORDERS_FILL_{lvl}_CONFIRM"
    kb = {
        "inline_keyboard": [[
            {"text": "CONFIRM", "callback_data": f"{cb}:{symbol}:{reserved}"},
            {"text": "↩️", "callback_data": f"ORDERS_FILL:{symbol}"},
        ]]
    }
    return msg, kb


def _confirm_fill_level(symbol: str, amount: int, lvl: str, title: str) -> Tuple[str, Dict[str, Any]]:
    """Подтверждение FILL: переводим резерв в spent."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректные параметры операции.", {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    week = int(info.get("week") or 0)

    levels = get_pair_levels(symbol, month)
    lvl_state = levels.get(lvl) or {}
    reserved = int(lvl_state.get("reserved") or 0)
    try:
        spent = int(lvl_state.get("spent") or 0)
    except Exception:
        spent = 0
    try:
        week_quota = int(lvl_state.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    try:
        last_fill_week = int(lvl_state.get("last_fill_week") if lvl_state.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1

    if reserved <= 0:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        msg = (
            f"{symbol} {mon_disp} Wk{week}\n"
            f"{title} FILL\n\n"
            f"Нечего помечать: резерв уже 0 USDC."
        )
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
    new_spent = spent + actual

    # помечаем, что исполнение было в текущую неделю
    if actual > 0 and week > 0:
        last_fill_week = week

    levels[lvl] = {
        "reserved": new_reserved,
        "spent": new_spent,
        "week_quota": week_quota,
        "last_fill_week": last_fill_week,
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
                    {"text": "OCO", "callback_data": f"ORDERS_FILL_OCO:{sym}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_FILL_L0:{sym}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_FILL_L1:{sym}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_FILL_L2:{sym}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_FILL_L3:{sym}"},
                ],
                [
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
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
            f"{title}: помечен как исполненный на {actual} USDC.\n"
            f"Бюджет: {info2.get('budget')} | "
            f"⏳ {info2.get('reserve')} | "
            f"💸 {info2.get('spent')} | "
            f"🎯 {info2.get('free')}"
        )
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
        return msg, kb


def perform_rollover(symbol: str) -> Dict[str, Any]:
    """Роловер недели: снять виртуальные ордера, перерасчитать недельные квоты и увеличить week."""

    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {}

    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    week = int(info.get("week") or 0)

    if budget <= 0 or week <= 0:
        # цикл не запущен
        return info

    # читаем уровни
    levels = get_pair_levels(symbol, month) or {}

    for lvl in LEVEL_KEYS:
        st = levels.get(lvl) or {}
        try:
            reserved = int(st.get("reserved") or 0)
        except Exception:
            reserved = 0
        try:
            spent = int(st.get("spent") or 0)
        except Exception:
            spent = 0
        try:
            week_quota = int(st.get("week_quota") or 0)
        except Exception:
            week_quota = 0
        try:
            last_fill_week = int(st.get("last_fill_week") if st.get("last_fill_week") is not None else -1)
        except Exception:
            last_fill_week = -1

        # базовая квота на следующую неделю
        base = _compute_base_quota(symbol, month, lvl, budget)

        had_fill = (last_fill_week == week)
        if had_fill:
            next_week_quota = base
        else:
            quota_prev = week_quota if week_quota > 0 else base
            next_week_quota = base + quota_prev
            if base > 0:
                max_quota = 4 * base
                if next_week_quota > max_quota:
                    next_week_quota = max_quota

        if next_week_quota < 0:
            next_week_quota = 0

        levels[lvl] = {
            "reserved": 0,  # все ордера снимаем → деньги вернутся в free
            "spent": spent,
            "week_quota": next_week_quota,
            "last_fill_week": -1,  # новая неделя — ещё не исполнялось
        }

    # сохраняем уровни и пересчитываем агрегаты
    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)

    # ensure week increment and fresh state
    info3 = info2
    try:
        new_week = week + 1
        set_pair_week(symbol, month, new_week)
        info3 = get_pair_budget(symbol, month)
    except Exception:
        # fallback: return aggregates before week increment if anything fails
        pass
# после ролловера пересчитаем флаги
    _recompute_symbol_flags(symbol)

    return info3


# -------------------------
# OPEN ALL helpers

def _calc_available_for_level(symbol: str, month: str, week: int, lvl: str, budget: int) -> int:
    """Доступная сумма к открытию по уровню с учётом квот и already used/filled этой недели."""
    levels = get_pair_levels(symbol, month) or {}
    base_quota = _compute_base_quota(symbol, month, lvl, budget)
    if base_quota <= 0:
        return 0
    st = levels.get(lvl) or {}
    try:
        week_quota = int(st.get("week_quota") or 0)
    except Exception:
        week_quota = 0
    quota = week_quota if week_quota > 0 else base_quota
    try:
        last_fill_week = int(st.get("last_fill_week") if st.get("last_fill_week") is not None else -1)
    except Exception:
        last_fill_week = -1
    reserved = int(st.get("reserved") or 0)
    spent_curr = int(st.get("spent") or 0) if last_fill_week == week else 0
    available = quota - (reserved + spent_curr)
    return available if available > 0 else 0


def prepare_open_all_limit(symbol: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка: открыть все лимитные уровни (🟡) на доступные суммы."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — ALL недоступен.", {}

    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    yellow = {k for k,v in (flags or {}).items() if v == "🟡"}
    levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in yellow]

    items = []
    total = 0
    for lvl in levels_list:
        a = _calc_available_for_level(symbol, month, week, lvl, budget)
        if a > 0:
            items.append((lvl, a))
            total += a

    if total <= 0:
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"}]]}
        return f"{symbol} {month}\nALL (лимит) — нечего открывать.", kb

    mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
    parts = ", ".join([f"{lvl} {amt}" for lvl,amt in items])
    msg = (f"{symbol} {mon_disp} Wk{week}\n⚠️ ALL (лимит)\n\n"
           f"Открыть {len(items)} ордера на сумму {total} USDC?\nСписок: {parts}")
    kb = {"inline_keyboard":[
        [{"text":"CONFIRM","callback_data":f"ORDERS_OPEN_ALL_LIMIT_CONFIRM:{symbol}"}],
        [{"text":"CANCEL","callback_data":f"ORDERS_OPEN_ALL_LIMIT_CANCEL:{symbol}"}],
    ]}
    return msg, kb


def confirm_open_all_limit(symbol: str) -> Tuple[str, Dict[str, Any]]:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    free = int(info.get("free") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — операция отменена.", {}

    levels = get_pair_levels(symbol, month) or {}
    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    yellow = {k for k,v in (flags or {}).items() if v == "🟡"}
    levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in yellow]

    applied = []
    total = 0
    for lvl in levels_list:
        a = _calc_available_for_level(symbol, month, week, lvl, budget)
        if a <= 0:
            continue
        if free <= 0:
            break
        actual = min(a, free)
        st = levels.get(lvl) or {}
        reserved = int(st.get("reserved") or 0)
        try:
            spent = int(st.get("spent") or 0)
        except Exception:
            spent = 0
        try:
            week_quota = int(st.get("week_quota") or 0)
        except Exception:
            week_quota = 0
        try:
            last_fill_week = int(st.get("last_fill_week") if st.get("last_fill_week") is not None else -1)
        except Exception:
            last_fill_week = -1
        levels[lvl] = {
            "reserved": reserved + actual,
            "spent": spent,
            "week_quota": week_quota,
            "last_fill_week": last_fill_week,
        }
        free -= actual
        total += actual
        applied.append((lvl, actual))

    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)
    _recompute_symbol_flags(symbol)

    if total <= 0:
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"}]]}
        return f"{symbol} {month}\nALL (лимит) — ничего не открыто.", kb

    mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
    parts = ", ".join([f"{lvl} {amt}" for lvl,amt in applied])
    msg = (f"{symbol} {mon_disp} Wk{week}\n⚠️ ALL (лимит)\n\n"
           f"Открыто {len(applied)} ордеров на сумму {total} USDC.\nСписок: {parts}")
    
    # После изменений пересобираем карточку и остаёмся в подменю OPEN
    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard":[
                [
                    {"text":"OCO","callback_data":f"ORDERS_OPEN_OCO:{sym}"},
                    {"text":"LIMIT 0","callback_data":f"ORDERS_OPEN_L0:{sym}"},
                    {"text":"LIMIT 1","callback_data":f"ORDERS_OPEN_L1:{sym}"},
                    {"text":"LIMIT 2","callback_data":f"ORDERS_OPEN_L2:{sym}"},
                    {"text":"LIMIT 3","callback_data":f"ORDERS_OPEN_L3:{sym}"},
                ],
                [
                    {"text":"✅ ALL","callback_data":f"ORDERS_OPEN_ALL_MKT:{sym}"},
                    {"text":"⚠️ ALL","callback_data":f"ORDERS_OPEN_ALL_LIMIT:{sym}"},
                    {"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        # Фоллбек: текстовое подтверждение, если сборка карточки упала
        mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
        return f"{symbol} {mon_disp}\nОперация выполнена.", kb



def prepare_open_all_mkt(symbol: str) -> Tuple[str, Dict[str, Any]]:
    """Подготовка: маркет-исполнение (🟢) всех доступных уровней на их квоты."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — ALL недоступен.", {}

    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    green = {k for k,v in (flags or {}).items() if v == "🟢"}
    levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in green]

    items = []
    total = 0
    for lvl in levels_list:
        a = _calc_available_for_level(symbol, month, week, lvl, budget)
        if a > 0:
            items.append((lvl, a))
            total += a

    if total <= 0:
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"}]]}
        return f"{symbol} {month}\n✅ ALL — нечего исполнять.", kb

    mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
    parts = ", ".join([f"{lvl} {amt}" for lvl,amt in items])
    msg = (f"{symbol} {mon_disp} Wk{week}\n✅ ALL (маркет)\n\n"
           f"Исполнить {len(items)} ордеров на сумму {total} USDC?\nСписок: {parts}")
    kb = {"inline_keyboard":[
        [{"text":"CONFIRM","callback_data":f"ORDERS_OPEN_ALL_MKT_CONFIRM:{symbol}"}],
        [{"text":"CANCEL","callback_data":f"ORDERS_OPEN_ALL_MKT_CANCEL:{symbol}"}],
    ]}
    return msg, kb


def confirm_open_all_mkt(symbol: str) -> Tuple[str, Dict[str, Any]]:
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    info = get_pair_budget(symbol, month)
    budget = int(info.get("budget") or 0)
    week = int(info.get("week") or 0)
    if week <= 0 or budget <= 0:
        return f"{symbol} {month}\nЦикл ещё не запущен — операция отменена.", {}

    levels = get_pair_levels(symbol, month) or {}
    sdata = _load_symbol_data(symbol)
    flags = compute_all_flags(sdata) if isinstance(sdata, dict) else {}
    green = {k for k,v in (flags or {}).items() if v == "🟢"}
    levels_list = [k for k in ("OCO","L0","L1","L2","L3") if k in green]

    applied = []
    total = 0
    for lvl in levels_list:
        a = _calc_available_for_level(symbol, month, week, lvl, budget)
        if a <= 0:
            continue
        st = levels.get(lvl) or {}
        reserved = int(st.get("reserved") or 0)
        try:
            spent = int(st.get("spent") or 0)
        except Exception:
            spent = 0
        try:
            week_quota = int(st.get("week_quota") or 0)
        except Exception:
            week_quota = 0
        # FILL: перевод в spent и фиксация недели
        levels[lvl] = {
            "reserved": reserved,
            "spent": spent + a,
            "week_quota": week_quota,
            "last_fill_week": week,
        }
        total += a
        applied.append((lvl, a))

    save_pair_levels(symbol, month, levels)
    info2 = recompute_pair_aggregates(symbol, month)
    _recompute_symbol_flags(symbol)

    if total <= 0:
        kb = {"inline_keyboard":[[{"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"}]]}
        return f"{symbol} {month}\n✅ ALL — ничего не исполнено.", kb

    mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
    parts = ", ".join([f"{lvl} {amt}" for lvl,amt in applied])
    msg = (f"{symbol} {mon_disp} Wk{week}\n✅ ALL (маркет)\n\n"
           f"Исполнено {len(applied)} на сумму {total} USDC.\nСписок: {parts}")
    
    # После изменений пересобираем карточку и остаёмся в подменю OPEN
    try:
        card = build_symbol_message(symbol)
        sym = (symbol or "").upper()
        kb = {
            "inline_keyboard":[
                [
                    {"text":"OCO","callback_data":f"ORDERS_OPEN_OCO:{sym}"},
                    {"text":"LIMIT 0","callback_data":f"ORDERS_OPEN_L0:{sym}"},
                    {"text":"LIMIT 1","callback_data":f"ORDERS_OPEN_L1:{sym}"},
                    {"text":"LIMIT 2","callback_data":f"ORDERS_OPEN_L2:{sym}"},
                    {"text":"LIMIT 3","callback_data":f"ORDERS_OPEN_L3:{sym}"},
                ],
                [
                    {"text":"✅ ALL","callback_data":f"ORDERS_OPEN_ALL_MKT:{sym}"},
                    {"text":"⚠️ ALL","callback_data":f"ORDERS_OPEN_ALL_LIMIT:{sym}"},
                    {"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        # Фоллбек: текстовое подтверждение, если сборка карточки упала
        mon_disp = f"{month[5:]}-{month[:4]}" if len(month)==7 and month[4]=="-" else month
        return f"{symbol} {mon_disp}\nОперация выполнена.", kb

# -------------------------

# Публичные обёртки для FILL
def prepare_fill_oco(symbol: str):  return _prepare_fill_level(symbol, "OCO", "OCO")
def confirm_fill_oco(symbol: str, amount: int):  return _confirm_fill_level(symbol, amount, "OCO", "OCO")

def prepare_fill_l0(symbol: str):   return _prepare_fill_level(symbol, "L0", "LIMIT 0")
def confirm_fill_l0(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L0", "LIMIT 0")

def prepare_fill_l1(symbol: str):   return _prepare_fill_level(symbol, "L1", "LIMIT 1")
def confirm_fill_l1(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L1", "LIMIT 1")

def prepare_fill_l2(symbol: str):   return _prepare_fill_level(symbol, "L2", "LIMIT 2")
def confirm_fill_l2(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L2", "LIMIT 2")

def prepare_fill_l3(symbol: str):   return _prepare_fill_level(symbol, "L3", "LIMIT 3")
def confirm_fill_l3(symbol: str, amount: int):   return _confirm_fill_level(symbol, amount, "L3", "LIMIT 3")

def prepare_cancel_all(symbol: str):
    """Подготовка отмены всех открытых (⚠️ reserved>0) ордеров: OCO, L0–L3."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректный символ.", {}
    month = datetime.now().strftime("%Y-%m")
    mon_disp = month
    if len(month) == 7 and month[4] == "-":
        mon_disp = f"{month[5:]}-{month[:4]}"
    levels = get_pair_levels(symbol, month)
    if not isinstance(levels, dict):
        levels = {}
    order_keys = ["OCO","L0","L1","L2","L3"]
    items = []
    total = 0
    for k in order_keys:
        st = levels.get(k) or {}
        r = int(st.get("reserved") or 0)
        if r > 0:
            items.append(f"{k} {r}")
            total += r
    if total <= 0:
        return (f"{symbol} {mon_disp}\n"
                f"❌ ALL — нечего отменять."), {
            "inline_keyboard":[
                [
                    {"text": "OCO", "callback_data": f"ORDERS_CANCEL_OCO:{symbol}"},
                    {"text": "LIMIT 0", "callback_data": f"ORDERS_CANCEL_L0:{symbol}"},
                    {"text": "LIMIT 1", "callback_data": f"ORDERS_CANCEL_L1:{symbol}"},
                    {"text": "LIMIT 2", "callback_data": f"ORDERS_CANCEL_L2:{symbol}"},
                    {"text": "LIMIT 3", "callback_data": f"ORDERS_CANCEL_L3:{symbol}"},
                ],
                [
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{symbol}"},
                    {"text":"↩️","callback_data":f"ORDERS_BACK_MENU:{symbol}"},
                ]
            ]
        }
    msg = (f"{symbol} {mon_disp}\n"
           f"❌ ALL (cancel)\n\n"
           f"Отменить {len(items)} ордера на сумму {total} USDC?\n"
           f"Список: {', '.join(items)}")
    kb = {
        "inline_keyboard":[[
            {"text":"CONFIRM","callback_data":f"ORDERS_CANCEL_ALL_CONFIRM:{symbol}"},
            {"text":"↩️","callback_data":f"ORDERS_CANCEL:{symbol}"},
        ]]
    }
    return msg, kb


def confirm_cancel_all(symbol: str):
    """Отмена всех открытых (⚠️) ордеров — reserved→0, пересбор карточки и подменю CANCEL."""
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return "Некорректные параметры операции.", {}
    month = datetime.now().strftime("%Y-%m")
    levels = get_pair_levels(symbol, month)
    if not isinstance(levels, dict):
        levels = {}
    changed = False
    total = 0
    for k in ["OCO","L0","L1","L2","L3"]:
        st = levels.get(k) or {}
        r = int(st.get("reserved") or 0)
        if r > 0:
            total += r
            changed = True
            levels[k] = {
                "reserved": 0,
                "spent": int(st.get("spent") or 0),
                "week_quota": int(st.get("week_quota") or 0),
                "last_fill_week": int(st.get("last_fill_week") or 0),
            }
    if not changed:
        # Нечего отменять — просто вернуть текущее подменю CANCEL
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
                        {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                        {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                    ],
                ]
            }
            return card, kb
        except Exception:
            return "❌ ALL — нечего отменять.", {}
    # Сохраняем и пересчитываем агрегаты/флаги
    save_pair_levels(symbol, month, levels)
    recompute_pair_aggregates(symbol, month)
    _recompute_symbol_flags(symbol)
    # Пересобираем карточку и остаёмся в CANCEL
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
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                    {"text":"❌ ALL","callback_data":f"ORDERS_CANCEL_ALL:{sym}"},
                    {"text": "↩️", "callback_data": f"ORDERS_BACK_MENU:{sym}"},
                ],
            ]
        }
        return card, kb
    except Exception:
        mon_disp = month
        if len(month) == 7 and month[4] == "-":
            mon_disp = f"{month[5:]}-{month[:4]}"
        return f"{symbol} {mon_disp}\nОтменено на сумму {total} USDC.", {}
