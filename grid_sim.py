import json
import logging
import os
import time
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List

from aiogram import Bot
from trade_mode import is_sim_mode
from dca_status import build_dca_status_text
from dca_handlers import _grid_path, _has_active_campaign

logger = logging.getLogger(__name__)


def _normalize_price(bar: Dict[str, Any], *keys: str) -> float:
    """
    Достаёт цену из bar по первому найденному ключу.
    Поддерживает варианты: "open"/"o", "high"/"h", "low"/"l", "close"/"c".
    """
    last_err: Optional[Exception] = None
    for key in keys:
        if key not in bar:
            continue
        value = bar.get(key)
        if value is None:
            continue
        try:
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str) and value.strip():
                return float(value)
        except (TypeError, ValueError) as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise KeyError(f"Price keys {keys} not found in bar")


def _extract_bar_ohlc(bar: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Нормализует структуру свечи к виду:
    {"ts", "open", "high", "low", "close"}.
    Возвращает None, если что‑то критичное отсутствует.
    """
    if not isinstance(bar, dict):
        return None

    ts = bar.get("ts") or bar.get("close_time") or bar.get("open_time")
    if ts is None:
        return None

    try:
        low = _normalize_price(bar, "low", "l")
        high = _normalize_price(bar, "high", "h")
        open_ = _normalize_price(bar, "open", "o")
        close = _normalize_price(bar, "close", "c")
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("simulate_bar_for_symbol: invalid bar OHLC: %s; error=%s", bar, e)
        return None

    try:
        ts_int = int(ts)
    except Exception:
        ts_int = int(time.time())

    return {
        "ts": ts_int,
        "low": float(low),
        "high": float(high),
        "open": float(open_),
        "close": float(close),
    }


def _grid_log_path() -> Path:
    """Путь к общему JSONL‑логу сеток."""
    storage_dir = os.environ.get("STORAGE_DIR", ".")
    return Path(storage_dir) / "grid_log.jsonl"


def _append_grid_event(event: Dict[str, Any]) -> None:
    """Пишем одно событие в grid_log.jsonl. Ошибки молча гасим."""
    try:
        path = _grid_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:  # pragma: no cover - логирование не критично
        logger.warning("Failed to append grid event: %s", e)


def _recalc_aggregates(grid: Dict[str, Any]) -> None:
    """Пересчитать агрегаты filled_levels / remaining_levels / spent_usdc / avg_price."""
    levels: List[Dict[str, Any]] = grid.get("current_levels") or grid.get("levels") or []
    if not isinstance(levels, list):
        levels = []

    total_levels = grid.get("total_levels")
    try:
        total_levels_int = int(total_levels)
        if total_levels_int < 0:
            raise ValueError
    except Exception:
        total_levels_int = len(levels)

    filled_levels = 0
    spent_usdc = 0.0
    total_qty = 0.0
    total_px_qty = 0.0

    for lvl in levels:
        try:
            filled = bool(lvl.get("filled"))
        except Exception:
            filled = False
        if not filled:
            continue
        filled_levels += 1
        try:
            notional = float(lvl.get("notional") or 0.0)
        except Exception:
            notional = 0.0
        spent_usdc += notional
        try:
            price = float(lvl.get("price") or 0.0)
            qty = float(lvl.get("qty") or 0.0)
        except Exception:
            price = 0.0
            qty = 0.0
        total_qty += max(qty, 0.0)
        total_px_qty += max(qty, 0.0) * price

    remaining_levels = max(total_levels_int - filled_levels, 0)

    if total_qty > 0:
        avg_price = total_px_qty / total_qty
    else:
        avg_price = None

    grid["total_levels"] = total_levels_int
    grid["filled_levels"] = filled_levels
    grid["remaining_levels"] = remaining_levels
    grid["spent_usdc"] = float(spent_usdc)
    grid["avg_price"] = avg_price


def _apply_initial_prefill(grid: Dict[str, Any], bar_ohlc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Единоразово (per grid) отмечает уровни выше/равные текущей цене как исполненные.

    Это фикс для симуляции: если часть уровней по своей цене уже должна была
    исполниться в момент старта кампании (уровень >= текущей цены),
    помечаем их filled до прохода по свечам, чтобы аггрегаторы (budget/levels)
    работали корректно.

    Возвращает список уровней, которые стали filled именно здесь (для логов).
    """
    if not isinstance(grid, dict):
        return []

    if grid.get("sim_prefill_done"):
        return []

    levels: List[Dict[str, Any]] = grid.get("current_levels") or grid.get("levels") or []
    if not isinstance(levels, list) or not levels:
        grid["sim_prefill_done"] = True
        return []

    try:
        current_price = float(bar_ohlc.get("open"))
    except Exception:
        grid["sim_prefill_done"] = True
        return []

    ts = int(bar_ohlc.get("ts") or time.time())
    newly_filled: List[Dict[str, Any]] = []

    for lvl in levels:
        try:
            if lvl.get("filled"):
                continue
            price = float(lvl.get("price") or 0.0)
        except Exception:
            continue

        # BUY‑логика: уровни, цена которых >= текущей, на реальной бирже
        # были бы исполнены как market / taker‑лимитки.
        if price >= current_price:
            lvl["filled"] = True
            lvl["filled_ts"] = ts
            newly_filled.append(lvl)

    # Отмечаем, что инициализация выполнена (даже если ничего не изменилось),
    # чтобы не пытаться повторить её на следующих свечах.
    grid["sim_prefill_done"] = True

    if newly_filled:
        _recalc_aggregates(grid)
        grid.setdefault("updated_ts", int(time.time()))
        # причину можно пометить для отладки
        grid.setdefault("closed_reason", grid.get("closed_reason"))

    return newly_filled


def _schedule_notify_closed(symbol: str) -> None:
    """Асинхронно уведомляем админа о завершении кампании.

    Ошибки логируем, но не пробрасываем.
    """
    try:
        token = os.environ.get("BOT_TOKEN")
        admin_chat_str = os.environ.get("ADMIN_CHAT_ID") or ""
        if not token or not admin_chat_str:
            return
        try:
            admin_chat_id = int(admin_chat_str)
        except ValueError:
            return

        loop = asyncio.get_event_loop()
        # создаём задачу, но не ждём её
        loop.create_task(_notify_campaign_closed(symbol, token, admin_chat_id))
    except Exception as e:  # pragma: no cover
        logger.warning("simulate_bar_for_symbol: failed to schedule notify for %s: %s", symbol, e)


async def _notify_campaign_closed(symbol: str, token: str, admin_chat_id: int) -> None:
    """Отправляем в админ‑чат уведомление о завершении кампании."""
    try:
        bot = Bot(token=token)
        text = build_dca_status_text(symbol)
        if not text:
            text = f"DCA-кампания для {symbol} завершена."
        await bot.send_message(chat_id=admin_chat_id, text=text)
        await bot.session.close()
    except Exception as e:  # pragma: no cover
        logger.warning("simulate_bar_for_symbol: failed to run notify for %s: %s", symbol, e)


def simulate_bar_for_symbol(symbol: str, bar: Dict[str, Any]) -> Optional[dict]:
    """Основной движок симуляции: применяет одну свечу TF1 к DCA‑сетке.

    1. Работает только в SIM‑режиме.
    2. Требует активной кампании для symbol.
    3. По первой свече дополнительно выполняет prefill уровней с ценой
       >= текущей цене (open), чтобы «мгновенные» уровни не висели
       неисполненными.
    4. Для каждой свечи отмечает новые filled‑уровни, пересчитывает агрегаты
       и при необходимости закрывает кампанию по бюджету/уровням.
    """
    symbol = (symbol or "").upper()
    if not symbol:
        return None

    # 1) Только SIM‑режим
    if not is_sim_mode():
        return None

    # 2) Должна быть активная кампания
    if not _has_active_campaign(symbol):
        return None

    ohlc = _extract_bar_ohlc(bar)
    if ohlc is None:
        return None

    gpath = _grid_path(symbol)
    try:
        raw_grid = gpath.read_text(encoding="utf-8")
        grid: Dict[str, Any] = json.loads(raw_grid)
    except Exception as e:
        logger.warning("simulate_bar_for_symbol: failed to read grid for %s: %s", symbol, e)
        return None

    # Убедимся, что бар не старше старта кампании (защита от мусора)
    try:
        start_ts = int(grid.get("campaign_start_ts") or 0)
    except Exception:
        start_ts = 0
    if start_ts and int(ohlc["ts"]) < start_ts:
        return None

    levels: List[Dict[str, Any]] = grid.get("current_levels") or grid.get("levels") or []
    if not isinstance(levels, list) or not levels:
        return grid

    # Снимок уже исполненных уровней до любых изменений (для логов)
    before_filled_indexes = {
        lvl.get("level_index")
        for lvl in levels
        if lvl.get("filled")
    }

    # 3) Единоразовый prefill по цене открытия первой свечи
    newly_prefilled = _apply_initial_prefill(grid, ohlc)

    # Если prefill уже полностью закрыл кампанию — дальше можно не идти.
    # Но для простоты: проверим campaign_end_ts после пересчёта агрегатов.
    try:
        campaign_end_ts = grid.get("campaign_end_ts")
    except Exception:
        campaign_end_ts = None

    # 4) Исполнение уровней по диапазону [low, high]
    low = float(ohlc["low"])
    high = float(ohlc["high"])
    bar_ts = int(ohlc["ts"])

    any_filled_on_bar = False
    for lvl in levels:
        try:
            if lvl.get("filled"):
                continue
            price = float(lvl.get("price") or 0.0)
        except Exception:
            continue

        # Стандартное условие: цена уровня попала в диапазон свечи
        if price < low or price > high:
            continue

        lvl["filled"] = True
        lvl["filled_ts"] = bar_ts
        any_filled_on_bar = True

    # Если не было ни новых filled (ни prefill, ни по диапазону) — просто сохраняем сетку и выходим.
    if not newly_prefilled and not any_filled_on_bar:
        try:
            gpath.write_text(json.dumps(grid, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("simulate_bar_for_symbol: failed to write grid for %s: %s", symbol, e)
        return grid

    # 5) Пересчитать агрегаты после всех изменений
    _recalc_aggregates(grid)
    grid["updated_ts"] = int(time.time())

    levels_after: List[Dict[str, Any]] = grid.get("current_levels") or grid.get("levels") or []
    filled_levels = int(grid.get("filled_levels") or 0)
    total_levels = int(grid.get("total_levels") or len(levels_after) or 0)
    spent_usdc = float(grid.get("spent_usdc") or 0.0)

    # 6) Логирование level_filled только для реально новых исполнений
    after_filled_indexes = {
        lvl.get("level_index")
        for lvl in levels_after
        if lvl.get("filled")
    }
    new_indexes = [idx for idx in after_filled_indexes if idx not in before_filled_indexes]

    if new_indexes:
        for lvl in levels_after:
            idx = lvl.get("level_index")
            if idx not in new_indexes:
                continue
            try:
                event = {
                    "event": "level_filled",
                    "symbol": symbol,
                    "ts": int(time.time()),
                    "grid_id": grid.get("grid_id"),
                    "level_index": idx,
                    "price": lvl.get("price"),
                    "qty": lvl.get("qty"),
                    "notional": lvl.get("notional"),
                    "filled_levels": filled_levels,
                    "total_levels": total_levels,
                    "spent_usdc": spent_usdc,
                    "source": "sim_bar",
                    "bar_ts": bar_ts,
                }
                _append_grid_event(event)
            except Exception as e:  # pragma: no cover
                logger.warning("simulate_bar_for_symbol: failed to log level_filled: %s", e)

    # 7) Проверка на автозакрытие по бюджету/уровням
    cfg = grid.get("config") or {}
    try:
        budget_usdc = float(cfg.get("budget_usdc") or 0.0)
    except Exception:
        budget_usdc = 0.0

    closed_now = False
    if not grid.get("campaign_end_ts"):
        closed_by_levels = total_levels > 0 and filled_levels >= total_levels
        closed_by_budget = budget_usdc > 0 and spent_usdc >= budget_usdc

        if closed_by_levels or closed_by_budget:
            now_ts = int(time.time())
            grid["campaign_end_ts"] = now_ts
            # Сохраняем текстовую причину для логов/отладки
            if closed_by_levels and closed_by_budget:
                grid["closed_reason"] = "budget_and_levels"
            elif closed_by_budget:
                grid["closed_reason"] = "budget"
            elif closed_by_levels:
                grid["closed_reason"] = "levels"
            else:
                grid["closed_reason"] = grid.get("closed_reason") or "auto"
            closed_now = True

            try:
                event = {
                    "event": "grid_budget_closed",
                    "symbol": symbol,
                    "ts": now_ts,
                    "grid_id": grid.get("grid_id"),
                    "campaign_start_ts": grid.get("campaign_start_ts"),
                    "campaign_end_ts": grid.get("campaign_end_ts"),
                    "filled_levels": filled_levels,
                    "total_levels": total_levels,
                    "spent_usdc": spent_usdc,
                    "avg_price": grid.get("avg_price"),
                    "reason": grid.get("closed_reason") or "auto",
                }
                _append_grid_event(event)
            except Exception as e:  # pragma: no cover
                logger.warning("simulate_bar_for_symbol: failed to log grid_budget_closed: %s", e)

    # 8) Сохраняем обновлённый grid
    try:
        gpath.write_text(json.dumps(grid, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("simulate_bar_for_symbol: failed to write grid for %s: %s", symbol, e)

    # 9) Если только что закрыли кампанию — уведомляем админа
    if closed_now:
        _schedule_notify_closed(symbol)

    return grid
