
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from dca_models import DCAConfigPerSymbol

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)
CONFIG_PATH = STORAGE_PATH / "dca_config.json"


def _ensure_storage_dir() -> None:
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)


def load_dca_config() -> Dict[str, DCAConfigPerSymbol]:
    """
    Загрузка общего конфига DCA из STORAGE_DIR/dca_config.json.

    Если файла нет — создаёт пустой JSON-объект {} и возвращает пустой словарь.
    """
    _ensure_storage_dir()
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text("{}", encoding="utf-8")
        return {}

    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        data = json.loads(raw)
    except Exception:
        # В случае битого JSON не пытаемся чинить автоматически — просто не используем его.
        return {}

    if not isinstance(data, dict):
        return {}

    result: Dict[str, DCAConfigPerSymbol] = {}
    for symbol, cfg_raw in data.items():
        if not isinstance(cfg_raw, dict):
            continue
        # Если в JSON не указан symbol, подставляем ключ из словаря.
        cfg_dict = dict(cfg_raw)
        cfg_dict.setdefault("symbol", symbol)
        try:
            cfg = DCAConfigPerSymbol.from_dict(cfg_dict)
        except Exception:
            continue
        result[symbol] = cfg
    return result


def save_dca_config(config: Dict[str, DCAConfigPerSymbol]) -> None:
    """
    Сохранение конфига DCA в dca_config.json.

    Ключи словаря — символы (строки), значения — DCAConfigPerSymbol.
    """
    _ensure_storage_dir()
    data = {symbol: cfg.to_dict() for symbol, cfg in config.items()}
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_symbol_config(symbol: str) -> Optional[DCAConfigPerSymbol]:
    """
    Удобный доступ к конфигу по конкретному символу.

    Символ нормализуется к верхнему регистру.
    """
    symbol = symbol.upper()
    all_cfg = load_dca_config()
    return all_cfg.get(symbol)


def upsert_symbol_config(cfg: DCAConfigPerSymbol) -> None:
    """
    Добавить или обновить конфиг по символу и сразу сохранить dca_config.json.
    """
    all_cfg = load_dca_config()
    symbol = cfg.symbol.upper()
    cfg.symbol = symbol
    all_cfg[symbol] = cfg
    save_dca_config(all_cfg)




def zero_symbol_budget(symbol: str) -> None:
    """
    Обнуляет budget_usdc для указанного символа в dca_config.json.
    Если символа нет в конфиге — ничего не делает.
    """
    symbol = symbol.upper()
    all_cfg = load_dca_config()
    cfg = all_cfg.get(symbol)
    if not cfg:
        return
    try:
        cfg.budget_usdc = 0.0
    except Exception:
        return
    all_cfg[symbol] = cfg
    save_dca_config(all_cfg)

def validate_budget_vs_min_notional(
    cfg: DCAConfigPerSymbol,
    min_notional: float,
) -> Tuple[bool, Optional[str]]:
    """
    Проверить правило:
        budget_usdc >= levels_count * minNotional

    Возвращает (ok, error_message). Если ok=True, error_message=None.
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
