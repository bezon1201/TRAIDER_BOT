import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        log.warning("Не удалось прочитать %s: %s", path, e)
        return None


def _state_path(symbol: str) -> Path:
    symbol_u = (symbol or "").upper()
    return DATA_DIR / f"{symbol_u}state.json"


def _grid_path(symbol: str) -> Path:
    symbol_u = (symbol or "").upper()
    return DATA_DIR / f"{symbol_u}_grid.json"


def _ticker_path(symbol: str) -> Path:
    symbol_u = (symbol or "").upper()
    return DATA_DIR / f"{symbol_u}.json"


def _dca_config_path() -> Path:
    return DATA_DIR / "dca_config.json"


def _fmt_money_usd(value: Optional[float]) -> str:
    if value is None:
        return "-"
    try:
        s = f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "-"
    return s.replace(",", " ") + "$"


def _fmt_price_usd(value: Optional[float]) -> str:
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    s = f"{v:,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", " ") + "$"


def _fmt_percent(value: Optional[float]) -> str:
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{v:.1f}%%"


def _fmt_dt_from_ts(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromtimestamp(float(ts))
    except (OSError, OverflowError, ValueError, TypeError):
        return "-"
    # Формат как в CARD1: 15/11/2025 10:35
    return dt.strftime("%d/%m/%Y %H:%M")


def _fmt_tf_pair(tf1: Optional[str], tf2: Optional[str]) -> str:
    def _clean(tf: Optional[str]) -> str:
        if not tf:
            return "-"
        tf = str(tf).strip()
        while tf and not tf[-1].isdigit():
            tf = tf[:-1]
        return tf or "-"

    left = _clean(tf1)
    right = _clean(tf2)
    if left == "-" and right == "-":
        return "-/-"
    return f"{left}/{right}"


def _fmt_market_mode(mode: Optional[str]) -> str:
    if not mode:
        return "-"
    m = str(mode).upper()
    emoji_map = {
        "DOWN": "⬇️",
        "UP": "⬆️",
        "RANGE": "🔄",
    }
    emoji = emoji_map.get(m, "")
    label = m.capitalize()
def _fmt_anchor_descr(cfg: Optional[Dict[str, Any]]) -> str:
    """Формирует короткое текстовое описание режима ANCHOR.

    Примеры:
    FIX -> "FIX"
    MA30 c offset -2% -> "MA30-2%"
    PRICE c offset +100 -> "PRICE+100"
    """
    if not cfg:
        return "-"

    mode = str(cfg.get("anchor_mode") or "FIX").upper()

    # Для FIX offset можно не показывать
    if mode == "FIX":
        return "FIX"

    offset_type = str(cfg.get("anchor_offset_type") or "ABS").upper()
    try:
        offset_value = float(cfg.get("anchor_offset_value") or 0.0)
    except (TypeError, ValueError):
        offset_value = 0.0

    if abs(offset_value) < 1e-9:
        # Нулевой offset: просто режим
        return mode

    sign = "+" if offset_value > 0 else "-"
    value_abs = abs(offset_value)
    value_str = f"{value_abs:g}"

    if offset_type == "PCT":
        return f"{mode}{sign}{value_str}%"

    # ABS
    return f"{mode}{sign}{value_str}"


    return f"{label} {emoji}".strip()


def _load_state(symbol: str) -> Dict[str, Any]:
    path = _state_path(symbol)
    data = _load_json(path)
    return data or {}


def _load_grid(symbol: str) -> Dict[str, Any]:
    path = _grid_path(symbol)
    data = _load_json(path)
    return data or {}


def _load_ticker(symbol: str) -> Dict[str, Any]:
    path = _ticker_path(symbol)
    data = _load_json(path)
    return data or {}


def _load_dca_config_for_symbol(symbol: str) -> Dict[str, Any]:
    symbol_u = (symbol or "").upper()
    path = _dca_config_path()
    all_cfg = _load_json(path) or {}
    if not isinstance(all_cfg, dict):
        return {}
    cfg = all_cfg.get(symbol_u) or all_cfg.get(symbol_u.upper())
    return cfg or {}


def build_symbol_card_text(symbol: Optional[str]) -> str:
    """Собирает текст карточки MAIN MENU по шаблону CARD1.

    Пример:

    BTCUSDC
    Start	15/11/2025 10:33
    Updated	18/11/2025 17:33
    Stop	20/11/2025 11:40

    Grid	1		Market Down⬇️
    Price	100 756$	MA30 100 000$
    Anchor	100 456$	Anchor MA30-2%
    Average 100 000$	Depth 6.4%
    Budget	200$		Spent 122$
    """
    if not symbol:
        return "Создайте список пар"

    symbol_u = str(symbol).upper()

    state = _load_state(symbol_u)
    grid = _load_grid(symbol_u)
    ticker = _load_ticker(symbol_u)
    cfg = _load_dca_config_for_symbol(symbol_u)

    # Start / Stop / Updated
    start_ts = grid.get("campaign_start_ts")
    stop_ts = grid.get("campaign_end_ts")
    updated_ts = grid.get("updated_ts")

    # Market / TF / MA30 из state
    market_mode = state.get("market_mode")
    tf1 = state.get("tf1")
    tf2 = state.get("tf2")
    ma30 = state.get("MA30")

    # Anchor / Budget из dca_config.json
    anchor_price = None
    budget_usdc = None
    if cfg:
        anchor_price = cfg.get("anchor_price")
        budget_usdc = cfg.get("budget_usdc")

    # Depth в % от anchor: используем current_depth_cycle из grid
    depth_cycle = grid.get("current_depth_cycle")
    depth_pct = None
    try:
        if anchor_price and depth_cycle and float(anchor_price) > 0:
            depth_pct = 100.0 * float(depth_cycle) / float(anchor_price)
    except (TypeError, ValueError, ZeroDivisionError):
        depth_pct = None

    # Spent / Average из grid
    spent_usdc = grid.get("spent_usdc")
    avg_price = grid.get("avg_price")

    # Current Price — ask/last/bid/price из ticker
    current_price = None
    if ticker:
        trading_params = ticker.get("trading_params") or {}
        price_block = {}
        if isinstance(trading_params, dict):
            price_block = trading_params.get("price") or {}
            if not isinstance(price_block, dict):
                price_block = {}
        for key in ("ask", "last", "bid", "price"):
            v = price_block.get(key)
            if v is not None:
                try:
                    current_price = float(v)
                    break
                except (TypeError, ValueError):
                    continue
        if current_price is None:
            for key in ("ask", "price", "last", "bid"):
                v = ticker.get(key)
                if v is not None:
                    try:
                        current_price = float(v)
                        break
                    except (TypeError, ValueError):
                        continue

    # Grid id
    grid_id = grid.get("current_grid_id")

    # Строковые представления
    start_str = _fmt_dt_from_ts(start_ts)
    updated_str = _fmt_dt_from_ts(updated_ts)
    stop_str = _fmt_dt_from_ts(stop_ts)

    market_str = _fmt_market_mode(market_mode)
    tf_str = _fmt_tf_pair(tf1, tf2)

    anchor_str = _fmt_money_usd(anchor_price)
    depth_str = _fmt_percent(depth_pct)

    budget_str = _fmt_money_usd(budget_usdc)
    spent_str = _fmt_money_usd(spent_usdc)

    avg_price_str = _fmt_money_usd(avg_price)
    current_price_str = _fmt_money_usd(current_price)
    ma30_str = _fmt_money_usd(ma30)

    grid_id_str = "-"
    try:
        if grid_id is not None:
            grid_id_str = str(int(grid_id))
    except (TypeError, ValueError):
        grid_id_str = "-"

    anchor_descr = _fmt_anchor_descr(cfg)

    left_cells = [
        f"Grid {grid_id_str}",
        f"Price {current_price_str}",
        f"Anchor {anchor_str}",
        f"Average {avg_price_str}",
        f"Budget {budget_str}",
    ]
    right_cells = [
        f"Market {market_str}",
        f"MA30 {ma30_str}",
        f"Anchor {anchor_descr}",
        f"Depth {depth_str}",
        f"Spent {spent_str}",
    ]
    max_left = max(len(cell) for cell in left_cells) if left_cells else 0
    bottom_lines = [
        f"{left.ljust(max_left)}   {right}"
        for left, right in zip(left_cells, right_cells)
    ]

    lines = [
        symbol_u,
        f"Start {start_str}",
        f"Updated {updated_str}",
        f"Stop {stop_str}",
        "",
        *bottom_lines,
    ]
    return "\n".join(lines)