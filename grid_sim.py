import json
import logging
from typing import Optional, Dict, Any

from trade_mode import is_sim_mode
from dca_handlers import _grid_path, _has_active_campaign

logger = logging.getLogger(__name__)


def simulate_bar_for_symbol(symbol: str, bar: Dict[str, Any]) -> Optional[dict]:
    """
    Симуляция исполнения DCA-сетки по одной свече TF1 для символа.

    На шаге 2.2.1 функция выполняет только подготовительные проверки и загрузку
    текущей сетки без изменения уровней и агрегатов.

    Возвращает:
    - dict с текущей сеткой кампании, если все условия выполнены;
    - None, если:
      - режим торговли не SIM,
      - нет активной кампании для symbol,
      - отсутствует или повреждён файл <SYMBOL>_grid.json,
      - свеча bar имеет некорректный формат.
    """
    symbol = (symbol or "").upper()
    if not symbol:
        return None

    # Разрешаем симуляцию только в режиме SIM
    if not is_sim_mode():
        return None

    # Проверяем, есть ли активная кампания для symbol
    if not _has_active_campaign(symbol):
        return None

    # Свеча должна быть словарём с OHLC-данными
    if not isinstance(bar, dict):
        return None

    # Временная метка: ts или close_time / open_time
    ts = bar.get("ts") or bar.get("close_time") or bar.get("open_time")
    if ts is None:
        return None

    # Проверяем наличие и корректность OHLC-полей
    try:
        _low = float(bar["low"])
        _high = float(bar["high"])
        _open = float(bar["open"])
        _close = float(bar["close"])
    except (KeyError, TypeError, ValueError):
        return None

    # Загружаем текущую сетку кампании
    gpath = _grid_path(symbol)
    try:
        raw = gpath.read_text(encoding="utf-8")
        grid = json.loads(raw)
    except Exception:
        # На этом шаге не логируем ошибку в отдельные файлы, просто считаем,
        # что симуляция для этой свечи невозможна.
        return None

    # Дополнительная защита: если кампания всё же помечена как завершённая,
    # симуляцию не выполняем.
    if grid.get("campaign_end_ts"):
        return None

    # На шаге 2.2.1 не меняем ни уровни, ни агрегаты, только возвращаем grid.
    return grid
