import json
import math
import os
from datetime import datetime, timezone

try:
    from trade_mode import get_trade_mode
except ImportError:  # fallback for tests / environments без trade_mode.py
    def get_trade_mode() -> str:
        return "sim"


def _load_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _short_tf(tf: str | None) -> str:
    if not tf:
        return "--"
    digits = "".join(ch for ch in tf if ch.isdigit())
    return digits or tf


def _format_price(value, tick_size: float | None = None) -> str:
    if value is None:
        return "--"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "--"

    if tick_size is None or tick_size <= 0:
        decimals = 2
    else:
        try:
            decimals = max(0, min(8, round(-math.log10(float(tick_size)))))
        except Exception:
            decimals = 2

    fmt = f"{{:,.{decimals}f}}"
    s = fmt.format(val).replace(",", " ")
    return f"{s}$"


def _format_usd(value) -> str:
    if value is None:
        return "--"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "--"
    s = f"{val:,.2f}".replace(",", " ")
    return f"{s}$"


def _format_date(ts: int | None) -> str:
    if not ts:
        return ".."
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%d-%m")
    except Exception:
        return ".."


def _detect_market_text(market_mode: str | None) -> str:
    if not market_mode:
        return "Market ?"
    mode = str(market_mode).upper()
    if mode == "UP":
        return "Market Up⬆️"
    if mode == "DOWN":
        return "Market Down⬇️"
    if mode == "RANGE":
        return "Market range🔄"
    return f"Market {market_mode}"


def _detect_stop_reason(grid: dict) -> str:
    campaign_end_ts = grid.get("campaign_end_ts")
    total_levels = grid.get("total_levels")
    remaining_levels = grid.get("remaining_levels")
    spent = grid.get("spent_usdc")
    config = grid.get("config") or {}
    budget = config.get("budget_usdc")

    if campaign_end_ts is None:
        return "Active"

    # сетка уже остановлена, пробуем угадать причину
    if total_levels is not None and remaining_levels == 0:
        return "Levels stop"

    try:
        if budget is not None and spent is not None and spent >= budget:
            return "Budget stop"
    except TypeError:
        pass

    return "Manual stop"


def _depth_string(grid: dict, state: dict | None) -> str:
    levels = grid.get("current_levels") or []
    if not levels or state is None:
        return "Depth --"

    last_level = max(levels, key=lambda l: l.get("level_index", 0))
    last_price = last_level.get("price")
    try:
        current_price = state["trading_params"]["price"]["last"]
    except Exception:
        return "Depth --"

    if not last_price or not current_price:
        return "Depth --"

    try:
        depth_pct = (last_price - current_price) / current_price * 100.0
    except ZeroDivisionError:
        return "Depth --"

    return f"Depth {depth_pct:.1f}%"


def build_dca_status_text(symbol: str, storage_dir: str | None = None) -> str:
    """
    Построить компактный текст статуса DCA-сетки для /dca status <symbol>.

    Возвращает чистый текст без обёртки <pre>...</pre>.
    В хендлерах Telegram нужно отправлять так:
        text = build_dca_status_text("BNBUSDC")
        await message.answer(f"<pre>{text}</pre>", parse_mode="HTML")
    """
    symbol = symbol.upper()

    if storage_dir is None:
        storage_dir = os.getenv("STORAGE_DIR", ".")

    grid_path = os.path.join(storage_dir, f"{symbol}_grid.json")
    state_path = os.path.join(storage_dir, f"{symbol}state.json")

    grid = _load_json(grid_path)
    state = _load_json(state_path)

    if not grid:
        header = f"{symbol} SIM❌ RUNNING✅ Grid ?"
        return "\n".join([header, "No grid data."])

    # ---- 1. Шапка ----
    mode_str = (get_trade_mode() or "sim").lower()
    if mode_str == "live":
        mode_text = "LIVE✅"
    else:
        mode_text = "SIM❌"

    campaign_end_ts = grid.get("campaign_end_ts")
    remaining_levels = grid.get("remaining_levels")
    total_levels = grid.get("total_levels")

    if campaign_end_ts is None and (remaining_levels is None or remaining_levels > 0):
        run_text = "RUNNING✅"
    else:
        run_text = "STOPPED❌"

    grid_id = grid.get("current_grid_id")
    if grid_id is None:
        grid_id_text = "Grid ?"
    else:
        grid_id_text = f"Grid {grid_id}"

    header_line = f"{symbol} {mode_text} {run_text} {grid_id_text}"

    # ---- 2. Даты / причина ----
    start_ts = grid.get("campaign_start_ts")
    stop_ts = grid.get("campaign_end_ts")

    start_col = f"Start {_format_date(start_ts)}"
    stop_col = f"Stop {_format_date(stop_ts)}" if stop_ts else "Stop .."
    reason_col = _detect_stop_reason(grid)

    # ---- 3. Market + TF ----
    market_mode = grid.get("current_market_mode") or (state.get("market_mode") if state else None)
    market_text = _detect_market_text(market_mode)

    tf1 = grid.get("tf1") or (state.get("tf1") if state else None)
    tf2 = grid.get("tf2") or (state.get("tf2") if state else None)
    tf_text = f"TF {_short_tf(tf1)}/{_short_tf(tf2)}"

    # ---- 4. Anchor / Depth ----
    anchor_label = "Anchor"
    anchor_price_val = None

    if state is not None:
        ma30 = state.get("MA30")
        if isinstance(ma30, (int, float)):
            anchor_label = "Anchor MA30"
            anchor_price_val = ma30

    if anchor_price_val is None:
        anchor_price_val = grid.get("current_anchor_price")

    tick_size = None
    if state is not None:
        try:
            sym_info = state["trading_params"]["symbol_info"]
            tick_size = sym_info.get("tick_size_f") or sym_info.get("tick_size")
        except Exception:
            pass

    anchor_price_str = _format_price(anchor_price_val, tick_size=tick_size)
    depth_str = _depth_string(grid, state)

    # ---- 5. Levels ----
    levels_col = f"Lvls: {total_levels if total_levels is not None else '--'}"
    filled_levels = grid.get("filled_levels")
    remaining_levels = grid.get("remaining_levels")

    filled_col = f"Fill: {filled_levels if filled_levels is not None else '--'}"
    togo_col = f"ToGo: {remaining_levels if remaining_levels is not None else '--'}"

    # ---- 6. Average / Current ----
    avg_price_val = grid.get("avg_price")
    if avg_price_val is None:
        avg_price_val = anchor_price_val

    avg_price_str = _format_price(avg_price_val, tick_size=tick_size)

    current_price_val = None
    if state is not None:
        try:
            current_price_val = state["trading_params"]["price"]["last"]
        except Exception:
            pass
    current_price_str = _format_price(current_price_val, tick_size=tick_size)

    # ---- 7. Budget / Spent ----
    config = grid.get("config") or {}
    budget_val = config.get("budget_usdc")
    spent_val = grid.get("spent_usdc")

    budget_str = _format_usd(budget_val)
    spent_str = _format_usd(spent_val)

    # ---- Собираем строки (кроме шапки) ----
    rows = [
        [start_col, "", ""],
        [stop_col, "", reason_col],
        [market_text, tf_text, ""],
        [anchor_label, anchor_price_str, depth_str],
        [levels_col, filled_col, togo_col],
        ["Avge/Price", avg_price_str, current_price_str],
        ["Budget", budget_str, spent_str],
    ]

    # выравниваем по ширине для первых двух колонок
    col_widths = [0, 0, 0]
    for row in rows:
        for i in range(3):
            cell = row[i] if i < len(row) else ""
            cell = "" if cell is None else str(cell)
            if i < 2:  # только первые две выравниваем
                col_widths[i] = max(col_widths[i], len(cell))

    body_lines: list[str] = []
    for row in rows:
        parts = []
        for i in range(3):
            cell = row[i] if i < len(row) else ""
            cell = "" if cell is None else str(cell)
            if i < 2:
                parts.append(cell.ljust(col_widths[i]))
            else:
                parts.append(cell)
        line = "  ".join(p for p in parts if p != "" or len(parts) == 1).rstrip()
        body_lines.append(line)

    return "\n".join([header_line, *body_lines])
