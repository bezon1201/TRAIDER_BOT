import json
import math
import os
from datetime import datetime, timezone

try:
    from trade_mode import get_trade_mode
except ImportError:
    # Fallback: если модуль недоступен, считаем что режим SIM
    def get_trade_mode() -> str:
        return "sim"


def _load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _short_tf(tf):
    if not tf:
        return "--"
    # Берём только цифры, "5m" -> "5"
    digits = "".join(ch for ch in str(tf) if ch.isdigit())
    return digits or str(tf)


def _format_price(value, tick_size=None):
    """
    Формат цены:
    - без знака валюты
    - пробел как разделитель тысяч
    - количество знаков после запятой берём из tick_size, если он есть
    """
    if value is None:
        return "--"

    try:
        v = float(value)
    except (TypeError, ValueError):
        return "--"

    # Определяем количество знаков после запятой из tick_size
    decimals = 2
    if tick_size:
        try:
            ts = float(tick_size)
            if ts > 0:
                decimals = max(0, min(8, round(-math.log10(ts))))
        except (TypeError, ValueError):
            pass

    fmt = f"{{:.{decimals}f}}"
    s = fmt.format(v)

    # Убираем хвостовые нули и точку
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    # Разделяем на целую и дробную часть
    if "." in s:
        int_part, frac_part = s.split(".", 1)
    else:
        int_part, frac_part = s, ""

    # Форматируем целую часть с пробелом как разделителем тысяч
    try:
        int_val = int(int_part)
        int_formatted = f"{int_val:,}".replace(",", " ")
    except ValueError:
        int_formatted = int_part

    if frac_part:
        return f"{int_formatted}.{frac_part}"
    return int_formatted


def _format_compact_number(value):
    """
    Для Budget / Spent:
    - если число "почти целое" -> без дроби
    - иначе до 2 знаков после запятой
    - разделитель тысяч пробел
    """
    if value is None:
        return "--"

    try:
        v = float(value)
    except (TypeError, ValueError):
        return "--"

    if abs(v - round(v)) < 1e-9:
        # Почти целое
        s = f"{int(round(v)):,}".replace(",", " ")
    else:
        s = f"{v:,.2f}".replace(",", " ")
        # убираем хвостовые нули
        if "." in s:
            s = s.rstrip("0").rstrip(".")
    return s


def _format_date(ts):
    if not ts:
        return ".."
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%d-%m")
    except Exception:
        return ".."


def _detect_market_text(market_mode):
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


def _detect_stop_reason(grid):
    campaign_end_ts = grid.get("campaign_end_ts")
    total_levels = grid.get("total_levels")
    remaining_levels = grid.get("remaining_levels")
    spent = grid.get("spent_usdc")
    config = grid.get("config") or {}
    budget = config.get("budget_usdc")

    # Активная кампания
    if campaign_end_ts is None:
        return "Active"

    # Сетка остановлена — пытаемся угадать причину
    try:
        if total_levels is not None and remaining_levels == 0:
            return "Levels stop"
    except Exception:
        pass

    try:
        if budget is not None and spent is not None and spent >= budget:
            return "Budget stop"
    except Exception:
        pass

    return "Manual stop"


def _compute_depth_pct(grid, state):
    levels = grid.get("current_levels") or []
    if not levels or state is None:
        return "--"

    # Берём последний уровень по level_index
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

    return f"{depth_pct:.1f}%"


def build_dca_status_text(symbol, storage_dir=None):
    """
    Построить компактный текст статуса DCA-сетки в формате, как в STATUS.txt:

    BNBUSDC SIM❌ RUNNING✅ Grid 2
    Start 16-11
    Stop ..\tManual stop
    Market Up⬆️\t\tTF 5/1
    Anchor MA30\t943 000\t-0.9%
    Lvls: 10\tFill: 5\tToGo: 5
    Avge/Price\t943 000\t948 000
    Budget\t60\t30

    Возвращает *только текст*, без <pre> обёртки.
    """
    symbol = symbol.upper()

    if storage_dir is None:
        storage_dir = os.getenv("STORAGE_DIR", ".")

    grid_path = os.path.join(storage_dir, f"{symbol}_grid.json")
    state_path = os.path.join(storage_dir, f"{symbol}state.json")

    grid = _load_json(grid_path)
    state = _load_json(state_path)

    if not grid:
        # Минимальный фоллбек
        return f"{symbol} SIM❌ STOPPED❌ Grid ?\nNo grid data"

    # ---- 1. Шапка ----
    mode_raw = (get_trade_mode() or "sim").lower()
    if mode_raw == "live":
        mode_text = "LIVE✅"
    else:
        mode_text = "SIM❌"

    campaign_end_ts = grid.get("campaign_end_ts")
    remaining_levels = grid.get("remaining_levels")

    if campaign_end_ts is None and (remaining_levels is None or remaining_levels > 0):
        run_text = "RUNNING✅"
    else:
        run_text = "STOPPED❌"

    grid_id = grid.get("current_grid_id")
    if grid_id is None:
        grid_id_str = "?"
    else:
        grid_id_str = str(grid_id)

    header_line = f"{symbol} {mode_text} {run_text} Grid {grid_id_str}"

    # ---- 2. Start / Stop + reason ----
    start_ts = grid.get("campaign_start_ts")
    stop_ts = grid.get("campaign_end_ts")

    start_date = _format_date(start_ts)
    stop_date = _format_date(stop_ts) if stop_ts else ".."

    line_start = f"Start {start_date}"

    reason = _detect_stop_reason(grid)
    line_stop = f"Stop {stop_date}\t{reason}"

    # ---- 3. Market / TF ----
    market_mode = grid.get("current_market_mode")
    if not market_mode and state:
        market_mode = state.get("market_mode")

    market_text = _detect_market_text(market_mode)

    tf1 = grid.get("tf1") or (state.get("tf1") if state else None)
    tf2 = grid.get("tf2") or (state.get("tf2") if state else None)
    tf_text = f"TF {_short_tf(tf1)}/{_short_tf(tf2)}"

    # Вторая колонка пустая, как в STATUS.txt (двойной таб)
    line_market = f"{market_text}\t\t{tf_text}"

    # ---- 4. Anchor / Depth ----
    anchor_label = "Anchor"
    anchor_price_value = None

    if state is not None:
        ma30 = state.get("MA30")
        if isinstance(ma30, (int, float)):
            anchor_label = "Anchor MA30"
            anchor_price_value = ma30

    if anchor_price_value is None:
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

    line_anchor = f"{anchor_label}\t{anchor_price_str}\t{depth_str}"

    # ---- 5. Levels / Filled / ToGo ----
    total_levels = grid.get("total_levels")
    filled_levels = grid.get("filled_levels")
    remaining_levels = grid.get("remaining_levels")

    total_str = str(total_levels) if total_levels is not None else "--"
    filled_str = str(filled_levels) if filled_levels is not None else "--"
    remaining_str = str(remaining_levels) if remaining_levels is not None else "--"

    line_levels = f"Lvls: {total_str}\tFill: {filled_str}\tToGo: {remaining_str}"

    # ---- 6. Average / Current ----
    avg_price_value = grid.get("avg_price")
    if avg_price_value is None:
        avg_price_value = anchor_price_value

    avg_price_str = _format_price(avg_price_value, tick_size=tick_size)

    current_price_value = None
    if state is not None:
        try:
            current_price_value = state["trading_params"]["price"]["last"]
        except Exception:
            pass

    current_price_str = _format_price(current_price_value, tick_size=tick_size)

    line_avg = f"Avge/Price\t{avg_price_str}\t{current_price_str}"

    # ---- 7. Budget / Spent ----
    config = grid.get("config") or {}
    budget_value = config.get("budget_usdc")
    spent_value = grid.get("spent_usdc")

    budget_str = _format_compact_number(budget_value)
    spent_str = _format_compact_number(spent_value)

    line_budget = f"Budget\t{budget_str}\t{spent_str}"

    lines = [
        header_line,
        line_start,
        line_stop,
        line_market,
        line_anchor,
        line_levels,
        line_avg,
        line_budget,
    ]

    return "\n".join(lines)
