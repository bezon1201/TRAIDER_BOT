
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


@dataclass
class DCAConfigPerSymbol:
    """
    Конфигурация DCA по одной торговой паре.

    На этом этапе это чистый контейнер данных без бизнес-логики.
    """
    symbol: str
    enabled: bool = True

    # Общий бюджет (в USDC), который мы готовы потратить на кампанию/месяц по этой паре.
    budget_usdc: float = 0.0

    # Сколько целевых уровней (ордеров) должно быть исполнено за кампанию.
    levels_count: int = 0

    # Информационное поле: под какой базовый таймфрейм (TF1) проектировался конфиг.
    base_tf: Optional[str] = None

    created_ts: Optional[int] = None
    updated_ts: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DCAConfigPerSymbol":
        # Защита от мусорных данных: используем только известные поля
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        clean: Dict[str, Any] = {}
        for k, v in (data or {}).items():
            if k in fields:
                clean[k] = v
        # symbol обязателен; если его нет в словаре, пусть отвалится с TypeError — это сигнал о некорректном JSON
        return cls(**clean)


@dataclass
class DCALevel:
    """
    Один уровень DCA-сетки (BUY-ордер).
    """
    level_index: int
    grid_id: int

    side: str = "BUY"          # BUY
    order_type: str = "LIMIT"  # пока предполагаем лимитные заявки

    price: float = 0.0
    qty: float = 0.0
    notional: float = 0.0      # price * qty в USDC

    status: str = "NEW"        # NEW / OPEN / FILLED / CANCELED / ...
    filled: bool = False

    created_ts: Optional[int] = None
    updated_ts: Optional[int] = None
    filled_ts: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DCALevel":
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        clean: Dict[str, Any] = {}
        for k, v in (data or {}).items():
            if k in fields:
                clean[k] = v
        return cls(**clean)


@dataclass
class DCAStatePerSymbol:
    """
    Текущее состояние DCA-кампании по одной паре.

    Здесь фиксируем:
    - общий прогресс кампании (total_levels / filled_levels / spent_usdc),
    - параметры текущей активной сетки (current_*),
    - привязку к TF1/TF2.
    """
    symbol: str

    tf1: Optional[str] = None
    tf2: Optional[str] = None

    campaign_start_ts: Optional[int] = None
    campaign_end_ts: Optional[int] = None  # если не None — кампания завершена

    # Снимок конфига на момент запуска кампании
    config: Optional[DCAConfigPerSymbol] = None

    total_levels: int = 0
    filled_levels: int = 0
    spent_usdc: float = 0.0

    # Текущая сетка между двумя publish/market force
    current_grid_id: int = 0
    current_market_mode: Optional[str] = None
    current_anchor_price: Optional[float] = None
    current_atr_tf1: Optional[float] = None
    current_depth_cycle: Optional[float] = None

    current_levels: List[DCALevel] = field(default_factory=list)

    created_ts: Optional[int] = None
    updated_ts: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Явно сериализуем вложенные структуры, чтобы не зависеть от реализации asdict
        if self.config is not None:
            data["config"] = self.config.to_dict()
        if self.current_levels is not None:
            data["current_levels"] = [lvl.to_dict() for lvl in self.current_levels]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DCAStatePerSymbol":
        data = dict(data or {})
        # Восстанавливаем вложенный config
        cfg_raw = data.get("config")
        if isinstance(cfg_raw, dict):
            data["config"] = DCAConfigPerSymbol.from_dict(cfg_raw)
        else:
            data["config"] = None

        # Восстанавливаем список уровней
        levels_raw = data.get("current_levels") or []
        levels: List[DCALevel] = []
        if isinstance(levels_raw, list):
            for item in levels_raw:
                if isinstance(item, dict):
                    try:
                        levels.append(DCALevel.from_dict(item))
                    except Exception:
                        # Пропускаем битые записи уровня
                        continue
        data["current_levels"] = levels

        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        clean: Dict[str, Any] = {}
        for k, v in data.items():
            if k in fields:
                clean[k] = v

        return cls(**clean)