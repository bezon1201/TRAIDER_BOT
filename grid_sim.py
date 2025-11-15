import json
import logging
import time
from typing import Optional, Dict, Any

from trade_mode import is_sim_mode
from dca_handlers import _grid_path, _has_active_campaign

logger = logging.getLogger(__name__)


def simulate_bar_for_symbol(symbol: str, bar: Dict[str, Any]) -> Optional[dict]:
    """
    Симуляция исполнения DCA-сетки по одной свече TF1 для символа.

    Шаги плана:
    - 2.2.1 — проверки режима SIM, наличия активной кампании и валидности свечи;
    - 2.2.2 — отметка уровней как FILLED по условию bar.low <= level.price;
    - 2.3   — пересчёт агрегатов (filled_levels, remaining_levels, spent_usdc,
              avg_price, updated_ts) и автозакрытие кампании по бюджету/уровням.

    Возвращает:
    - dict с текущей (возможо обновлённой) сеткой кампании;
    - None, если симуляция для данной свечи невозможна.
    """
    # Нормализуем символ
    symbol = (symbol or "").upper()
    if not symbol:
        return None

    # 1) Разрешаем симуляцию только в режиме SIM
    if not is_sim_mode():
        return None

    # 2) Проверяем, есть ли активная кампания для symbol
    if not _has_active_campaign(symbol):
        return None

    # 3) Свеча должна быть словарём с OHLC-данными
    if not isinstance(bar, dict):
        return None

    # 4) Временная метка: ts или close_time / open_time
    bar_ts = bar.get("ts") or bar.get("close_time") or bar.get("open_time")
    if bar_ts is None:
        return None

    # 5) Проверяем наличие и корректность OHLC-полей
    try:
        low = float(bar["low"])
        high = float(bar["high"])
        open_ = float(bar["open"])
        close = float(bar["close"])
    except (KeyError, TypeError, ValueError):
        return None

    # 6) Загружаем текущую сетку кампании
    gpath = _grid_path(symbol)
    try:
        raw = gpath.read_text(encoding="utf-8")
        grid = json.loads(raw)
    except Exception:
        # На этом шаге не логируем ошибку в отдельные файлы, просто считаем,
        # что симуляция для этой свечи невозможна.
        return None

    # Дополнительная защита: если кампания уже завершена, симуляцию не выполняем.
    if grid.get("campaign_end_ts"):
        return None

    # 7) Отмечаем уровни, которые должны считаться FILLED на этой свече.
    levels = grid.get("current_levels") or []
    if not isinstance(levels, list):
        # Неожиданный формат — не трогаем сетку.
        return grid

    updated = False

    for level in levels:
        if not isinstance(level, dict):
            continue

        # Идемпотентность: если уже filled == true, второй раз не трогаем.
        if level.get("filled"):
            continue

        try:
            lvl_price = float(level["price"])
        except (KeyError, TypeError, ValueError):
            continue

        # Условие исполнения уровня (шаг 2.2.2):
        # если bar["low"] <= level["price"] → уровень считается исполненным.
        if low <= lvl_price:
            level["filled"] = True
            level["filled_ts"] = bar_ts
            updated = True

    # Если уровни не изменились — просто возвращаем исходную сетку.
    if not updated:
        return grid

    # 8) Пересчёт агрегатов после каждого FILLED (шаг 2.3).
    total_levels = grid.get("total_levels")
    try:
        total_levels_int = int(total_levels)
    except Exception:
        total_levels_int = len(levels)

    filled_levels = 0
    spent_usdc = 0.0
    total_qty = 0.0
    price_x_qty_sum = 0.0

    for level in levels:
        if not isinstance(level, dict):
            continue
        if not level.get("filled"):
            continue

        filled_levels += 1

        try:
            notional = float(level.get("notional") or 0.0)
        except Exception:
            notional = 0.0
        spent_usdc += notional

        try:
            qty = float(level.get("qty") or 0.0)
            price = float(level.get("price") or 0.0)
        except Exception:
            qty = 0.0
            price = 0.0

        if qty > 0:
            total_qty += qty
            price_x_qty_sum += price * qty

    remaining_levels = max(0, total_levels_int - filled_levels)

    if total_qty > 0:
        avg_price_val = price_x_qty_sum / total_qty
        avg_price = round(avg_price_val, 8)
    else:
        avg_price = None

    grid["total_levels"] = total_levels_int
    grid["filled_levels"] = filled_levels
    grid["remaining_levels"] = remaining_levels
    grid["spent_usdc"] = round(spent_usdc, 2)
    grid["avg_price"] = avg_price
    grid["updated_ts"] = int(time.time())

    # 9) Автозакрытие кампании по бюджету/уровням (шаг 2.3.1).
    cfg = grid.get("config") or {}
    try:
        budget_usdc = float(cfg.get("budget_usdc") or 0.0)
    except Exception:
        budget_usdc = 0.0

    need_close = False
    if filled_levels >= total_levels_int and total_levels_int > 0:
        need_close = True
    elif budget_usdc > 0 and spent_usdc >= budget_usdc:
        need_close = True

    if need_close:
        now_ts = int(time.time())
        grid["campaign_end_ts"] = now_ts
        # remaining_levels уже рассчитан выше как total_levels_int - filled_levels

    # 10) Сохраняем обновлённую сетку обратно в файл.
    try:
        gpath.write_text(json.dumps(grid, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # Если по какой-то причине не удалось сохранить, считаем, что симуляция
        # для этой свечи не удалась.
        return None

    return grid
