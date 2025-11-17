import json
import math
import os
from datetime import datetime, timezone


def _get_trade_mode() -> str:
    """Прочитать текущий режим торговли из trade_mode.json.

    Если файл отсутствует или повреждён — вернуть "sim".
    """
    storage_dir = os.environ.get("STORAGE_DIR", ".")
    path = os.path.join(storage_dir, "trade_mode.json")
    if not os.path.exists(path):
        return "sim"

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mode = (data.get("mode") or "sim").lower()
        if mode in ("sim", "live"):
            return mode
    except Exception:
        # На любых ошибках не падаем, а используем sim
        pass

    return "sim"


def _load_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _short_tf(tf):
    if not tf:
        return "--"
    s = str(tf)
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits or s


def _format_int_usd(value):
    """Округление до целого, без разделителей, со знаком $ (например 943000$)."""
    if value is None:
        return "--"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "--"
    return f"{int(round(v))}$"


def _format_date(ts):
    if not ts:
        return "-"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        # без ведущих нулей: 6-11
        return f"{dt.day}-{dt.month}"
    except Exception:
        return "-"


def _detect_market_text(market_mode: str | None) -> str:
    if not market_mode:
        return "?"
    mode = str(market_mode).upper()
    if mode == "UP":
        return "Up ⬆️"
    if mode == "DOWN":
        return "Down ⬇️"
    if mode == "RANGE":
        return "Range 🔄"
    return str(market_mode)


def _detect_stop_reason(grid: dict) -> str:
    campaign_end_ts = grid.get("campaign_end_ts")
    total_levels = grid.get("total_levels")
    remaining_levels = grid.get("remaining_levels")
    spent = grid.get("spent_usdc")
    config = grid.get("config") or {}
    budget = config.get("budget_usdc")

    if campaign_end_ts is None:
        return "Active ✅"

    # кампания завершена, определяем причину
    try:
        if total_levels is not None and remaining_levels == 0:
            return "Levels stop ❌"
    except Exception:
        pass

    try:
        if budget is not None and spent is not None and spent >= budget:
            return "Budget stop ❌"
    except Exception:
        pass

    return "Manual stop ❌"


def _compute_depth_pct(grid: dict, state: dict | None) -> str:
    levels = grid.get("current_levels") or []
    if not levels or state is None:
        return "--"

    # последний уровень по level_index, иначе последний в списке
    try:
        last_level = max(levels, key=lambda l: l.get("level_index", 0))
    except Exception:
        last_level = levels[-1]

    last_price = last_level.get("price")
    try:
        current_price = state["trading_params"]["price"]["last"]
    except Exception:
        return "--"

    if not last_price or not current_price:
        return "--"

    try:
        depth_pct = (last_price - current_price) / current_price * 100.0
    except ZeroDivisionError:
        return "--"

    return f"{depth_pct:.2f}%"


def build_dca_status_text(symbol: str, storage_dir: str | None = None) -> str:
    """
    Собирает статус DCA-сетки в формате, описанном пользователем:

    1: SYMBOL \t (LIVE/SIM) \t Grid <id>
    2: Start \t <start_date>
    3: Stop \t <stop_date> \t <reason>
    4: Market \t Up/Down/Range+emoji \t TF tf1/tf2
    5: Anchor MA30|Price \t <anchor_price>$ \t <depth%>
    6: Lvls: N \t Fill: M \t ToGo: K
    7: Avge/Price \t <avg_price>$ \t <current_price>$
    8: Budget <budget>$ \t Spent <spent>$
    """
    symbol = symbol.upper()

    if storage_dir is None:
        storage_dir = os.getenv("STORAGE_DIR", ".")

    grid_path = os.path.join(storage_dir, f"{symbol}_grid.json")
    state_path = os.path.join(storage_dir, f"{symbol}state.json")

    grid = _load_json(grid_path)
    state = _load_json(state_path)

    if not grid:
        return f"{symbol}\tSIM ❌\tGrid ?\nNo grid data"

    # 1. Шапка
    mode_raw = (_get_trade_mode() or "sim").lower()
    if mode_raw == "live":
        mode_text = "LIVE ✅"
    else:
        mode_text = "SIM ❌"

    grid_id = grid.get("current_grid_id")
    grid_id_str = str(grid_id) if grid_id is not None else "?"
    line1 = f"{symbol}\t{mode_text}\tGrid {grid_id_str}"

    # 2. Start
    start_ts = grid.get("campaign_start_ts")
    start_date = _format_date(start_ts)
    line2 = f"Start\t{start_date}"

    # 3. Stop + reason
    stop_ts = grid.get("campaign_end_ts")
    stop_date = _format_date(stop_ts) if stop_ts else "-"
    reason = _detect_stop_reason(grid)
    line3 = f"Stop\t{stop_date}\t{reason}"

    # 4. Market / TF
    market_mode = grid.get("current_market_mode") or (state.get("market_mode") if state else None)
    market_col2 = _detect_market_text(market_mode)

    tf1 = grid.get("tf1") or (state.get("tf1") if state else None)
    tf2 = grid.get("tf2") or (state.get("tf2") if state else None)
    tf_text = f"TF {_short_tf(tf1)}/{_short_tf(tf2)}"
    line4 = f"Market\t{market_col2}\t{tf_text}"

    # 5. Anchor / Depth
    anchor_label = "Anchor MA30"
    anchor_price_value = None

    if state is not None:
        ma30 = state.get("MA30")
        if isinstance(ma30, (int, float)):
            anchor_price_value = ma30

    if anchor_price_value is None:
        anchor_label = "Price"
        anchor_price_value = grid.get("current_anchor_price") or None

    anchor_price_str = _format_int_usd(anchor_price_value)
    depth_str = _compute_depth_pct(grid, state)
    line5 = f"{anchor_label}\t{anchor_price_str}\t{depth_str}"

    # 6. Levels / Filled / ToGo
    total_levels = grid.get("total_levels")
    filled_levels = grid.get("filled_levels")
    remaining_levels = grid.get("remaining_levels")

    # Если кампания уже завершена, подменяем общее количество уровней
    # на актуальное значение из DCA-конфига.
    if grid.get("campaign_end_ts"):
        try:
            from dca_config import get_symbol_config
            cfg = get_symbol_config(symbol)
        except Exception:
            cfg = None

        if cfg is not None:
            lv = getattr(cfg, "levels_count", None)
            if lv is not None:
                total_levels = lv

    total_str = str(total_levels) if total_levels is not None else "--"
    filled_str = str(filled_levels) if filled_levels is not None else "--"
    remaining_str = str(remaining_levels) if remaining_levels is not None else "--"

    line6 = f"Lvls: {total_str}\tFill: {filled_str}\tToGo: {remaining_str}"

    # 7. Average / current price
    avg_price_value = grid.get("avg_price") or anchor_price_value
    avg_price_str = _format_int_usd(avg_price_value)

    current_price_value = None
    if state is not None:
        try:
            current_price_value = state["trading_params"]["price"]["last"]
        except Exception:
            pass
    current_price_str = _format_int_usd(current_price_value)

    line7 = f"Avge/Price\t{avg_price_str}\t{current_price_str}"

    # 8. Budget / Spent
    config = grid.get("config") or {}
    budget_value = config.get("budget_usdc")
    spent_value = grid.get("spent_usdc")

    # Если кампания уже завершена, используем актуальный DCA-budget
    # из конфига, а не старое значение, зашитое в последнюю сетку.
    if grid.get("campaign_end_ts"):
        try:
            from dca_config import get_symbol_config
            cfg = get_symbol_config(symbol)
        except Exception:
            cfg = None

        if cfg is not None:
            bv = getattr(cfg, "budget_usdc", None)
            if bv is not None:
                budget_value = bv

    budget_str = _format_int_usd(budget_value)
    spent_str = _format_int_usd(spent_value)

    line8 = f"Budget {budget_str}\tSpent {spent_str}"

    return "\n".join([
        line1,
        line2,
        line3,
        line4,
        line5,
        line6,
        line7,
        line8,
    ])
