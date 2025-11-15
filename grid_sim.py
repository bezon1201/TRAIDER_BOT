import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any

from trade_mode import is_sim_mode
from dca_handlers import _grid_path, _has_active_campaign, STORAGE_DIR

logger = logging.getLogger(__name__)

GRID_LOG_PATH = Path(STORAGE_DIR) / "grid_log.jsonl"


def _append_grid_log(event: Dict[str, Any]) -> None:
    """Добавить строку в grid_log.jsonl.

    Лог-файл общий для всех символов, поэтому обязательно указываем
    symbol и grid_id в самом событии.
    """
    try:
        GRID_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with GRID_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False))
            f.write("\n")
    except Exception:
        # Логирование ошибок логгера не должно ломать симуляцию
        logger.exception("Не удалось записать событие в %s", GRID_LOG_PATH)


def _extract_bar_ts(bar: Dict[str, Any]) -> Optional[int]:
    """Вытащить timestamp из свечи (ts / close_time / open_time)."""
    for key in ("ts", "close_time", "open_time"):
        if key in bar:
            try:
                return int(bar[key])
            except Exception:
                continue
    return None


def simulate_bar_for_symbol(symbol: str, bar: Dict[str, Any]) -> Optional[dict]:
    """Симуляция исполнения DCA-сетки по одной свече TF1 для символа.

    Шаги:
    - Проверяем режим торговли (только SIM).
    - Проверяем, что есть активная кампания для symbol.
    - Загружаем <SYMBOL>_grid.json.
    - Отмечаем все уровни, которые должны считаться FILLED для этой свечи.
    - Пересчитываем агрегаты (filled_levels, remaining_levels, spent_usdc, avg_price).
    - При необходимости закрываем кампанию по бюджету/уровням.
    - Логируем события level_filled и grid_budget_closed в grid_log.jsonl.

    Возвращает обновлённый dict сетки или None, если симуляция не выполнялась.
    """
    # --- Базовые проверки ---
    symbol = (symbol or "").upper()
    if not symbol:
        return None

    if not isinstance(bar, dict):
        return None

    if not is_sim_mode():
        # В боевом режиме симуляция по свече не выполняется
        return None

    if not _has_active_campaign(symbol):
        # Нет активной кампании для символа
        return None

    bar_ts = _extract_bar_ts(bar)
    if bar_ts is None:
        return None

    try:
        low = float(bar.get("low"))
        high = float(bar.get("high"))
        _ = float(bar.get("open"))
        _ = float(bar.get("close"))
    except Exception:
        # Свеча битая — симуляцию пропускаем
        return None

    # --- Загрузка текущей сетки ---
    gpath = _grid_path(symbol)
    if not gpath.exists():
        return None

    try:
        raw = gpath.read_text(encoding="utf-8")
        grid = json.loads(raw)
    except Exception:
        return None

    # Если кампания уже завершена, ничего не делаем
    if grid.get("campaign_end_ts"):
        return None

    levels = grid.get("current_levels") or []
    if not isinstance(levels, list):
        return grid

    # --- Отметка FILLED-уровней для этой свечи ---
    filled_now = []  # уровни, которые стали FILLED на этой свече

    for level in levels:
        if not isinstance(level, dict):
            continue

        if level.get("filled"):
            # Уже был исполнен ранее
            continue

        try:
            lvl_price = float(level.get("price"))
        except Exception:
            continue

        # По ТЗ: если low <= level.price — уровень считается исполненным
        if low <= lvl_price:
            level["filled"] = True
            level["filled_ts"] = bar_ts
            filled_now.append(level)

    # Если никаких новых FILLED нет — агрегаты и логи не трогаем
    if not filled_now:
        return grid

    # --- Пересчёт агрегатов после FILLED (2.3) ---
    total_levels = grid.get("total_levels")
    try:
        total_levels_int = int(total_levels) if total_levels is not None else len(levels)
    except Exception:
        total_levels_int = len(levels)

    filled_levels = 0
    spent_usdc = 0.0
    total_qty = 0.0
    sum_price_qty = 0.0

    for level in levels:
        if not isinstance(level, dict) or not level.get("filled"):
            continue

        filled_levels += 1

        try:
            notional = float(level.get("notional", 0.0))
        except Exception:
            notional = 0.0
        spent_usdc += notional

        try:
            qty = float(level.get("qty", 0.0))
            price = float(level.get("price", 0.0))
        except Exception:
            qty = 0.0
            price = 0.0

        if qty > 0.0:
            total_qty += qty
            sum_price_qty += price * qty

    if total_qty > 0.0:
        avg_price = sum_price_qty / total_qty
    else:
        avg_price = None

    remaining_levels = max(0, total_levels_int - filled_levels)

    grid["total_levels"] = total_levels_int
    grid["filled_levels"] = filled_levels
    grid["remaining_levels"] = remaining_levels
    grid["spent_usdc"] = spent_usdc
    grid["avg_price"] = avg_price
    grid["updated_ts"] = int(time.time())

    # --- Автозакрытие кампании по бюджету/уровням (2.3.1) ---
    cfg = grid.get("config") or {}
    try:
        budget_usdc = float(cfg.get("budget_usdc", 0.0) or 0.0)
    except Exception:
        budget_usdc = 0.0

    now_ts = int(time.time())
    campaign_was_open = not grid.get("campaign_end_ts")

    should_close = False
    if total_levels_int > 0 and filled_levels >= total_levels_int:
        should_close = True
    elif budget_usdc > 0.0 and spent_usdc >= budget_usdc:
        should_close = True

    if should_close and campaign_was_open:
        grid["campaign_end_ts"] = now_ts

    # --- Логирование событий в grid_log.jsonl (2.4) ---
    campaign_start_ts = grid.get("campaign_start_ts")
    campaign_end_ts = grid.get("campaign_end_ts")
    grid_id = grid.get("current_grid_id")

    # 2.4.1. level_filled — по каждому уровню, который стал FILLED на этой свече
    for level in filled_now:
        try:
            ev = {
                "event": "level_filled",
                "symbol": symbol,
                "ts": now_ts,
                "grid_id": level.get("grid_id", grid_id),
                "level_index": level.get("level_index"),
                "price": level.get("price"),
                "qty": level.get("qty"),
                "notional": level.get("notional"),
                "campaign_start_ts": campaign_start_ts,
                "campaign_end_ts": campaign_end_ts,
                "filled_levels": filled_levels,
                "total_levels": total_levels_int,
                "spent_usdc": spent_usdc,
                "avg_price": avg_price,
                "source": "sim_bar",
                "bar_ts": bar_ts,
            }
            _append_grid_log(ev)
        except Exception:
            logger.exception("Не удалось залогировать level_filled для %s", symbol)

    # 2.4.2. grid_budget_closed — если кампания только что закрылась
    if should_close and campaign_was_open:
        try:
            ev_close = {
                "event": "grid_budget_closed",
                "symbol": symbol,
                "ts": now_ts,
                "grid_id": grid_id,
                "campaign_start_ts": campaign_start_ts,
                "campaign_end_ts": campaign_end_ts,
                "filled_levels": filled_levels,
                "total_levels": total_levels_int,
                "spent_usdc": spent_usdc,
                "avg_price": avg_price,
            }
            _append_grid_log(ev_close)
        except Exception:
            logger.exception("Не удалось залогировать grid_budget_closed для %s", symbol)

    # --- Сохраняем обновлённую сетку ---
    try:
        gpath.write_text(json.dumps(grid, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("Не удалось сохранить обновлённый grid %s", gpath)
        return None

    return grid
