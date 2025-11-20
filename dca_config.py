from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from dca_models import DCAConfigPerSymbol
from config import STORAGE_DIR, TF1

STORAGE_PATH = Path(STORAGE_DIR)
CONFIG_PATH = STORAGE_PATH / "dca_config.json"


def _ensure_storage_dir() -> None:
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)


def load_dca_config() -> Dict[str, DCAConfigPerSymbol]:
    """Загрузка конфига DCA из dca_config.json.

    Возвращает словарь {SYMBOL: DCAConfigPerSymbol}.
    """
    _ensure_storage_dir()
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text("{}", encoding="utf-8")
        return {}

    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8") or "{}"
        data = json.loads(raw)
    except Exception:
        CONFIG_PATH.write_text("{}", encoding="utf-8")
        return {}

    if not isinstance(data, dict):
        return {}

    result: Dict[str, DCAConfigPerSymbol] = {}
    for symbol, cfg_dict in data.items():
        if not isinstance(symbol, str) or not isinstance(cfg_dict, dict):
            continue
        try:
            cfg = DCAConfigPerSymbol.from_dict(cfg_dict)
        except Exception:
            continue
        result[symbol.upper()] = cfg

    return result


def save_dca_config(config: Dict[str, DCAConfigPerSymbol]) -> None:
    """Сохранение конфига DCA в dca_config.json."""
    _ensure_storage_dir()
    data = {symbol: cfg.to_dict() for symbol, cfg in config.items()}
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_symbol_config(symbol: str) -> Optional[DCAConfigPerSymbol]:
    """Получить конфиг по конкретному symbol (регистр неважен)."""
    symbol = symbol.upper()
    config = load_dca_config()
    return config.get(symbol)


def upsert_symbol_config(cfg: DCAConfigPerSymbol) -> None:
    """Добавить или обновить конфиг для symbol."""
    config = load_dca_config()
    symbol = cfg.symbol.upper()
    cfg.symbol = symbol

    # Если базовый таймфрейм не задан — используем текущий TF1
    if not cfg.base_tf:
        cfg.base_tf = TF1

    # Всегда обновляем отметку времени последнего изменения
    cfg.updated_ts = int(time.time())

    config[symbol] = cfg
    save_dca_config(config)


def zero_symbol_budget(symbol: str) -> None:
    """Обнулить budget_usdc для symbol (используется при остановке кампании)."""
    symbol = symbol.upper()
    config = load_dca_config()
    cfg = config.get(symbol)
    if not cfg:
        return
    cfg.budget_usdc = 0.0
    save_dca_config(config)


def validate_budget_vs_min_notional(
    cfg: DCAConfigPerSymbol,
    min_notional: float,
) -> Tuple[bool, Optional[str]]:
    """Проверка, что budget_usdc достаточен с учётом levels_count и minNotional.

    Условие: budget_usdc >= levels_count * minNotional.
    """
    if min_notional <= 0:
        return False, "minNotional должен быть больше нуля."

    if cfg.levels_count <= 0:
        return False, "levels_count должен быть положительным."

    required = cfg.levels_count * float(min_notional)
    if cfg.budget_usdc < required:
        return False, (
            f"Недостаточный budget_usdc для {cfg.symbol}: "
            f"нужно не меньше {required:.8f} USDC "
            f"(levels_count={cfg.levels_count} × minNotional={min_notional})."
        )

    return True, None
