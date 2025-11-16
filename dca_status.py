import json
import math
import os
from datetime import datetime, timezone

try:
    from trade_mode import get_trade_mode
except ImportError:
    # Fallback stub: if trade_mode is not available, assume SIM mode.
    def get_trade_mode() -> str:
        return "sim"


def _load_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _short_tf(tf: str | None) -> str:
    if not tf:
        return "--"
    digits = "".join(ch for ch in tf if ch.isdigit())
    return digits or tf


def _format_price(value: float | None, tick_size: float | None = None) -> str:
    if value is None:
        return "--"
    if tick_size is None or tick_size <= 0:
        decimals = 2
    else:
        # derive decimals from tick_size (e.g. 0.01 -> 2, 0.1 -> 1)
        try:
            decimals = max(0, min(8, round(-math.log10(tick_size))))
        except Exception:
            decimals = 2
    fmt = f"{{:,.{decimals}f}}"
    s = fmt.format(value)
    # replace comma with space for thousands separator
    s = s.replace(",", " ")
    return f"{s}$"


def _format_usd(value: float | None) -> str:
    if value is None:
        return "--"
    # For budget/spent можно чуть грубее: 2 знака после запятой
    s = f"{value:,.2f}".replace(",", " ")
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
    mode = market_mode.upper()
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


def _compute_depth_pct(grid: dict, state: dict | None) -> str:
    levels = grid.get("current_levels") or []
    if not levels or state is None:
        return "Depth --"

    # последний уровень по level_index (или просто последний в списке)
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
    Построить текст статуса DCA-сетки для /dca status <symbol>.

    Возвращает чистый текст без обёртки <pre>...</pre>.
    В хендлере Telegram нужно будет отправить так:
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
        return f"{symbol} SIM❌ RUNNING✅ Grid id: ?\nNo grid data."

    # ---- 1. Шапка ----
    mode = (get_trade_mode() or "sim").lower()
    if mode == "live":
        mode_text = "LIVE✅"
    else:
        mode_text = "SIM❌"

    # Статус сетки
    campaign_end_ts = grid.get("campaign_end_ts")
    remaining_levels = grid.get("remaining_levels")
    total_levels = grid.get("total_levels")

    if campaign_end_ts is None and (remaining_levels is None or remaining_levels > 0):
        run_text = "RUNNING✅"
    else:
        run_text = "STOPPED❌"

    grid_id = grid.get("current_grid_id")
    if grid_id is None:
        grid_id_text = "Grid id: ?"
    else:
        grid_id_text = f"Grid id: {grid_id}"

    header_line = f"{symbol} {mode_text} {run_text} {grid_id_text}"

    # ---- 2. Даты и причина ----
    start_ts = grid.get("campaign_start_ts")
    stop_ts = grid.get("campaign_end_ts")

    start_date = _format_date(start_ts)
    stop_date = _format_date(stop_ts) if stop_ts else ".."

    start_col = f"Start {start_date}"
    stop_col = f"Stop {stop_date}"
    reason_col = _detect_stop_reason(grid)

    # ---- 3. Market + TF ----
    market_mode = grid.get("current_market_mode")
    if not market_mode and state:
        market_mode = state.get("market_mode")

    market_text = _detect_market_text(market_mode)

    tf1 = grid.get("tf1") or (state.get("tf1") if state else None)
    tf2 = grid.get("tf2") or (state.get("tf2") if state else None)
    tf_text = f"TF {_short_tf(tf1)}/{_short_tf(tf2)}"

    # ---- 4. Anchor / Depth ----
    # anchor label: пробуем взять MA30 из state, иначе просто Anchor
    anchor_label = "Anchor"
    anchor_price_value = None

    if state is not None:
        ma30 = state.get("MA30")
        if isinstance(ma30, (int, float)):
            anchor_label = "Anchor MA30"
            anchor_price_value = ma30

    if anchor_price_value is None:
        # fallback: current_anchor_price из grid
        anchor_price_value = grid.get("current_anchor_price")

    tick_size = None
    if state is not None:
        try:
            sym_info = state["trading_params"]["symbol_info"]
            tick_size = sym_info.get("tick_size_f") or sym_info.get("tick_size")
        except Exception:
            pass

    anchor_price_str = _format_price(anchor_price_value, tick_size=tick_size)
    depth_str = _compute_depth_pct(grid, state)

    # ---- 5. Levels / Filled / ToGo ----
    total_levels = grid.get("total_levels")
    filled_levels = grid.get("filled_levels")
    remaining_levels = grid.get("remaining_levels")

    levels_col = f"Levels: {total_levels if total_levels is not None else '--'}"
    filled_col = f"Filled: {filled_levels if filled_levels is not None else '--'}"
    to_go_col = f"ToGo: {remaining_levels if remaining_levels is not None else '--'}"

    # ---- 6. Average / Current ----
    avg_price_value = grid.get("avg_price")
    if avg_price_value is None:
        # если ещё не посчитана средняя — используем anchor_price как приближение
        avg_price_value = anchor_price_value

    avg_price_str = _format_price(avg_price_value, tick_size=tick_size)

    current_price_value = None
    if state is not None:
        try:
            current_price_value = state["trading_params"]["price"]["last"]
        except Exception:
            pass

    current_price_str = _format_price(current_price_value, tick_size=tick_size)
    current_col = f"Current {current_price_str}"

    # ---- 7. Budget / Spent ----
    config = grid.get("config") or {}
    budget_value = config.get("budget_usdc")
    spent_value = grid.get("spent_usdc")

    budget_str = _format_usd(budget_value)
    spent_str = _format_usd(spent_value)
    spent_col = f"Spent {spent_str}"

    # ---- Выравнивание по колонкам (кроме шапки) ----
    # Строки 2–7: по 3 логических колонки
    rows = [
        [start_col,   stop_col,      reason_col],
        [market_text, tf_text,       ""],
        [anchor_label, anchor_price_str, depth_str],
        [levels_col,  filled_col,    to_go_col],
        ["Average",   avg_price_str, current_col],
        ["Budget",    budget_str,    spent_col],
    ]

    # Считаем максимальную ширину для каждой колонки
    col_widths = [0, 0, 0]
    for row in rows:
        for i in range(3):
            cell = row[i] if i < len(row) else ""
            if cell is None:
                cell = ""
            col_widths[i] = max(col_widths[i], len(str(cell)))

    # Строим строки с выравниванием (кроме последней колонки — там только левая часть)
    body_lines: list[str] = []
    for row in rows:
        padded_parts = []
        for i in range(3):
            cell = row[i] if i < len(row) else ""
            cell = "" if cell is None else str(cell)
            if i < 2:
                padded_parts.append(cell.ljust(col_widths[i]))
            else:
                padded_parts.append(cell)
        line = "  ".join(padded_parts).rstrip()
        body_lines.append(line)

    # Склеиваем финальный текст
    return "\n".join([header_line, *body_lines])
