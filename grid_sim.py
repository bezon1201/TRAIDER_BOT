import json
import logging
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any

from trade_mode import is_sim_mode
from dca_handlers import _grid_path, _has_active_campaign

logger = logging.getLogger(__name__)


def _normalize_price(bar: Dict[str, Any], long_key: str, short_key: str) -> float:
    """
    Достаёт цену из бара, поддерживая оба варианта ключей:
    - "open" / "high" / "low" / "close"
    - "o" / "h" / "l" / "c"
    Бросает исключение, если значение не найдено или некорректно.
    """
    value = bar.get(long_key)
    if value is None:
        value = bar.get(short_key)
    if value is None:
        raise KeyError(long_key)
    return float(value)


def _extract_bar_ohlc(bar: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Нормализует структуру свечи к виду:
    {"ts", "open", "high", "low", "close"}.
    Возвращает None, если что‑то критичное отсутствует.
    """
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

    return {
        "ts": ts,
        "low": low,
        "high": high,
        "open": open_,
        "close": close,
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


def simulate_bar_for_symbol(symbol: str, bar: Dict[str, Any]) -> Optional[dict]:
    """
    Симуляция исполнения DCA‑сетки по одной свече TF1 для символа.

    Алгоритм (Шаг 2.2 + 2.3):

    1) Проверки:
       - работаем только в режиме SIM;
       - должна быть активная кампания (campaign_end_ts is null);
       - должен существовать <SYMBOL>_grid.json.

    2) Нормализуем бар к виду {ts, open, high, low, close}.

    3) Для каждого уровня current_levels[]:
       если filled == false и bar.low <= level.price → считаем FILLED,
       ставим filled = true, filled_ts = bar_ts.

    4) После каждого FILLED пересчитываем агрегаты:
       filled_levels, remaining_levels, spent_usdc, avg_price, updated_ts.

    5) Если filled_levels == total_levels или spent_usdc >= budget_usdc,
       закрываем кампанию по бюджету/уровням (campaign_end_ts = now)
       и пишем событие grid_budget_closed.

    6) Все новые FILLED логируем как события level_filled.
    """
    symbol = (symbol or "").upper()
    if not symbol:
        return None

    # 1. Проверяем режим торговли и наличие активной кампании
    if not is_sim_mode():
        return None

    if not _has_active_campaign(symbol):
        return None

    gpath = _grid_path(symbol)
    if not gpath.exists():
        return None

    # 2. Нормализуем бар
    norm_bar = _extract_bar_ohlc(bar)
    if norm_bar is None:
        return None

    bar_ts = norm_bar["ts"]
    bar_low = norm_bar["low"]
    bar_high = norm_bar["high"]

    # 3. Загружаем текущую сетку
    try:
        raw = gpath.read_text(encoding="utf-8")
        grid = json.loads(raw)
    except Exception as e:
        logger.warning("simulate_bar_for_symbol: failed to read grid for %s: %s", symbol, e)
        return None

    # Дополнительная защита: если кампания уже завершена — ничего не делаем
    if grid.get("campaign_end_ts") not in (None, 0):
        return None

    levels = grid.get("current_levels") or []
    if not levels:
        # Нечего симулировать
        return grid

    total_levels = grid.get("total_levels") or len(levels)
    grid["total_levels"] = total_levels

    cfg = grid.get("config") or {}
    budget_usdc = cfg.get("budget_usdc", grid.get("budget_usdc")) or 0.0
    try:
        budget_usdc = float(budget_usdc)
    except (TypeError, ValueError):
        budget_usdc = 0.0

    # 4. Отмечаем FILLED‑уровни (идемпотентно)
    newly_filled = []
    for level in levels:
        if level.get("filled") is True:
            continue

        try:
            price = float(level.get("price"))
        except (TypeError, ValueError):
            continue

        # Условие исполнения: минимум свечи коснулся цены уровня
        if bar_low <= price <= bar_high:
            level["filled"] = True
            level["filled_ts"] = bar_ts
            newly_filled.append(level)

    if not newly_filled:
        # Для этой свечи ничего нового не заполнилось
        return grid

    # 5. Пересчёт агрегатов после всех FILLED по этой свече
    filled_levels = sum(1 for lvl in levels if lvl.get("filled"))
    grid["filled_levels"] = filled_levels

    remaining_levels = total_levels - filled_levels
    if remaining_levels < 0:
        remaining_levels = 0
    grid["remaining_levels"] = remaining_levels

    spent_usdc = 0.0
    total_qty = 0.0
    weighted_sum = 0.0
    for lvl in levels:
        if not lvl.get("filled"):
            continue
        try:
            notional = float(lvl.get("notional", 0.0))
            qty = float(lvl.get("qty", 0.0))
            price = float(lvl.get("price", 0.0))
        except (TypeError, ValueError):
            continue

        spent_usdc += notional
        total_qty += qty
        weighted_sum += price * qty

    grid["spent_usdc"] = spent_usdc
    if total_qty > 0:
        grid["avg_price"] = weighted_sum / total_qty
    else:
        grid["avg_price"] = None

    now_ts = int(time.time())
    grid["updated_ts"] = now_ts

    # 6. Проверяем автозакрытие кампании по бюджету/уровням
    closed_by_budget = False
    if filled_levels >= total_levels or (budget_usdc > 0 and spent_usdc >= budget_usdc):
        if grid.get("campaign_end_ts") in (None, 0):
            grid["campaign_end_ts"] = now_ts
            closed_by_budget = True

    # Сохраняем обновлённую сетку на диск
    try:
        gpath.write_text(json.dumps(grid, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("simulate_bar_for_symbol: failed to write grid for %s: %s", symbol, e)
        # Даже если не смогли записать, возвращаем обновлённый grid в память
        return grid

    # 7. Логируем события level_filled
    grid_id = grid.get("current_grid_id", 1)
    for lvl in newly_filled:
        try:
            event = {
                "event": "level_filled",
                "symbol": symbol,
                "ts": now_ts,
                "grid_id": grid_id,
                "level_index": lvl.get("level_index"),
                "price": lvl.get("price"),
                "qty": lvl.get("qty"),
                "notional": lvl.get("notional"),
                "filled_levels": filled_levels,
                "total_levels": total_levels,
                "spent_usdc": spent_usdc,
                "source": "dca_simulate",
                "bar_ts": bar_ts,
            }
            _append_grid_event(event)
        except Exception as e:  # pragma: no cover - не ломаем симуляцию из‑за логов
            logger.warning("simulate_bar_for_symbol: failed to log level_filled: %s", e)

    # 8. Логируем grid_budget_closed (если закрыли кампанию)
    if closed_by_budget:
        try:
            event = {
                "event": "grid_budget_closed",
                "symbol": symbol,
                "ts": now_ts,
                "grid_id": grid_id,
                "campaign_start_ts": grid.get("campaign_start_ts"),
                "campaign_end_ts": grid.get("campaign_end_ts"),
                "filled_levels": filled_levels,
                "total_levels": total_levels,
                "spent_usdc": spent_usdc,
                "avg_price": grid.get("avg_price"),
            }
            _append_grid_event(event)
        except Exception as e:  # pragma: no cover
            logger.warning("simulate_bar_for_symbol: failed to log grid_budget_closed: %s", e)

    return grid
