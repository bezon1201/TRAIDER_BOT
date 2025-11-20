from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


@dataclass
class DCAConfigPerSymbol:
    """Конфигурация DCA по одной торговой паре."""

    symbol: str
    budget_usdc: float = 0.0
    levels_count: int = 0
    anchor_price: float = 0.0
    anchor_mode: str = "FIX"
    anchor_offset_value: float = 0.0
    anchor_offset_type: str = "ABS"
    enabled: bool = False
    base_tf: str = ""
    updated_ts: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DCAConfigPerSymbol":
        """Создаёт конфиг из dict с мягкой обратной совместимостью."""
        if not isinstance(data, dict):
            raise TypeError("DCAConfigPerSymbol.from_dict ожидает dict")

        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        clean: Dict[str, Any] = {}
        for k, v in data.items():
            if k in fields:
                clean[k] = v

        # Обратная совместимость: если anchor_mode/offset отсутствуют — ставим дефолты
        if "anchor_mode" not in clean:
            clean["anchor_mode"] = "FIX"
        if "anchor_offset_value" not in clean:
            clean["anchor_offset_value"] = 0.0
        if "anchor_offset_type" not in clean:
            clean["anchor_offset_type"] = "ABS"

        # Нормализуем symbol
        if "symbol" in clean and isinstance(clean["symbol"], str):
            clean["symbol"] = clean["symbol"].upper()

        return cls(**clean)


@dataclass
class DCAStatePerSymbol:
    """Состояние DCA-кампании по одной паре.

    Хранится в файле <SYMBOL>_grid.json и используется для статусов/ролловера.
    """

    symbol: str
    tf1: str
    tf2: str

    # Служебные таймстемпы
    created_ts: Optional[int] = None
    updated_ts: Optional[int] = None

    # Параметры текущей кампании
    campaign_start_ts: Optional[int] = None
    campaign_end_ts: Optional[int] = None

    # Последний рассчитанный anchor кампании (фактический, с учётом режима/offset)
    campaign_anchor: float = 0.0

    # Текущий шаг/уровень DCA
    current_level: int = 0
    max_levels: int = 0

    # Произвольный статус кампании
    status: str = "NEW"  # NEW / FILLED / CANCELED / ...

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DCAStatePerSymbol":
        if not isinstance(data, dict):
            raise TypeError("DCAStatePerSymbol.from_dict ожидает dict")

        # Объединяем данные с дефолтами датакласса
        defaults = asdict(cls(symbol="", tf1="", tf2=""))  # type: ignore[call-arg]
        merged = {**defaults, **data}

        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        clean: Dict[str, Any] = {}
        for k, v in merged.items():
            if k in fields:
                clean[k] = v

        # Обязательные поля
        if "symbol" not in clean and "symbol" in merged:
            clean["symbol"] = merged["symbol"]
        if "tf1" not in clean and "tf1" in merged:
            clean["tf1"] = merged["tf1"]
        if "tf2" not in clean and "tf2" in merged:
            clean["tf2"] = merged["tf2"]

        # Нормализуем symbol
        if "symbol" in clean and isinstance(clean["symbol"], str):
            clean["symbol"] = clean["symbol"].upper()

        return cls(**clean)


def _normalize_anchor_offset_type(offset_type: str) -> str:
    """Приводим тип offset к одному из значений: ABS | PCT.
    Любые другие варианты трактуем как ABS."""
    if not offset_type:
        return "ABS"
    t = str(offset_type).upper()
    if t == "PCT":
        return "PCT"
    return "ABS"


def apply_anchor_offset(base: float, offset_value: float, offset_type: str) -> float:
    """Применяет смещение к базовой цене.

    base           — базовая величина (MA30 или PRICE),
    offset_value   — смещение (может быть отрицательным),
    offset_type    — "ABS" (абсолют) или "PCT" (проценты).
    """
    offset_type = _normalize_anchor_offset_type(offset_type)

    if offset_type == "PCT":
        # offset_value трактуем как проценты, например:
        #  2.5  -> +2.5%
        # -3.0  -> -3%
        return base * (1.0 + (offset_value / 100.0))

    # ABS: просто сдвиг в единицах цены
    return base + offset_value


def compute_anchor_from_config(
    cfg: "DCAConfigPerSymbol",
    *,
    last_price: Optional[float] = None,
    ma30_value: Optional[float] = None,
) -> Optional[float]:
    """Вычисляет итоговый anchor по конфигу.

    Режимы:
      - FIX   — используется cfg.anchor_price
      - MA30  — берём ma30_value и применяем offset
      - PRICE — берём last_price и применяем offset

    last_price / ma30_value сюда будут подставляться снаружи
    (из <SYMBOL>state.json и модуля с MA30 соответственно).

    Возвращает:
      - float — если anchor удалось посчитать,
      - None  — если не хватает данных (нет PRICE/MA30 и т.п.).
    """
    mode = (cfg.anchor_mode or "FIX").upper()

    # 1) FIX: просто фиксированное значение из конфига
    if mode == "FIX":
        return cfg.anchor_price if cfg.anchor_price > 0 else None

    # 2) MA30: базой служит скользящая средняя
    if mode == "MA30":
        if ma30_value is None or ma30_value <= 0:
            # Нет данных MA30 — anchor посчитать не можем
            return None
        base = ma30_value
        return apply_anchor_offset(base, cfg.anchor_offset_value, cfg.anchor_offset_type)

    # 3) PRICE: базой служит текущая цена рынка (last)
    if mode == "PRICE":
        if last_price is None or last_price <= 0:
            # Нет last price — anchor посчитать не можем
            return None
        base = last_price
        return apply_anchor_offset(base, cfg.anchor_offset_value, cfg.anchor_offset_type)

    # На всякий случай, если в файле что-то странное:
    # падаем обратно к FIX-поведению
    return cfg.anchor_price if cfg.anchor_price > 0 else None
