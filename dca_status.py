from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import STORAGE_DIR
from dca_models import DCAStatePerSymbol

STORAGE_PATH = Path(STORAGE_DIR)


def _get_trade_mode() -> str:
    """Прочитать текущий режим торговли из trade_mode.json.

    Если файл отсутствует или повреждён — вернуть "sim".
    """
    path = STORAGE_PATH / "trade_mode.json"
    if not path.exists():
        return "sim"

    try:
        raw = path.read_text(encoding="utf-8") or "{}"
        data = json.loads(raw)
    except Exception:
        return "sim"

    mode = str(data.get("mode", "sim")).lower()
    return "live" if mode == "live" else "sim"


def _fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)


def build_dca_status_text(symbol: str) -> str:
    """Сформировать человекочитаемый статус DCA-кампании по symbol."""
    symbol = symbol.upper()
    grid_path = STORAGE_PATH / f"{symbol}_grid.json"
    if not grid_path.exists():
        return f"DCA: для {symbol} активная сетка не найдена."

    try:
        raw = grid_path.read_text(encoding="utf-8") or "{}"
        grid_data = json.loads(raw)
    except Exception:
        return f"DCA: не удалось прочитать состояние сетки для {symbol}."

    try:
        grid = DCAStatePerSymbol.from_dict(grid_data)
    except Exception:
        return f"DCA: повреждён формат состояния сетки для {symbol}."

    trade_mode = _get_trade_mode().upper()

    total_levels = grid.total_levels or len(grid.current_levels)
    filled_levels = grid.filled_levels or sum(
        1 for lvl in grid.current_levels if str(lvl.status).upper() == "FILLED"
    )
    to_go = max(total_levels - filled_levels, 0)

    budget = grid.config.budget_usdc
    spent = grid.spent_usdc

    # Средняя цена заполненных уровней
    filled_qty = 0.0
    filled_notional = 0.0
    for lvl in grid.current_levels:
        if str(lvl.status).upper() == "FILLED":
            filled_qty += float(lvl.qty)
            filled_notional += float(lvl.notional)
    avg_price = filled_notional / filled_qty if filled_qty > 0 else 0.0

    depth_pct = grid.grid_depth_pct
    if not depth_pct and grid.grid_depth_abs and grid.current_anchor_price:
        depth_pct = grid.grid_depth_abs / grid.current_anchor_price * 100.0

    lines = []

    lines.append(
        f"1: {grid.symbol} \t ({trade_mode}) \t Grid {grid.campaign_id or '-'}"
    )
    lines.append(
        f"2: Start \t {_fmt_ts(grid.campaign_start_ts)}"
    )
    stop_line = f"3: Stop \t {_fmt_ts(grid.campaign_end_ts)}"
    if grid.closed_reason:
        stop_line += f" \t {grid.closed_reason}"
    lines.append(stop_line)

    lines.append(
        f"4: Market \t {grid.current_market_mode or '-'} \t TF {grid.tf1}/{grid.tf2}"
    )

    lines.append(
        f"5: Anchor {grid.grid_anchor_type or 'ANCHOR'} \t "
        f"{grid.current_anchor_price:.4f} $ \t depth {depth_pct:.2f}%"
    )

    lines.append(
        f"6: Lvls: {total_levels} \t Fill: {filled_levels} \t ToGo: {to_go}"
    )

    lines.append(
        f"7: Avge/Price \t {avg_price:.4f} $ \t {grid.current_price:.4f} $"
    )

    lines.append(
        f"8: Budget {budget:.2f} $ \t Spent {spent:.2f} $"
    )

    return "\n".join(lines)
