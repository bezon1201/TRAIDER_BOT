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
        if not isinstance(data, dict):
            raise TypeError("data must be a dict")

        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        clean: Dict[str, Any] = {}
        for k, v in data.items():
            if k in fields:
                clean[k] = v

        # symbol обязателен
        if "symbol" not in clean and "symbol" in data:
            clean["symbol"] = data["symbol"]

        return cls(**clean)


@dataclass
class DCALevel:
    """Один уровень DCA-сетки."""

    index: int
    price: float
    qty: float
    notional: float  # price * qty в USDC

    status: str = "NEW"  # NEW / FILLED / CANCELED / ...
    created_ts: Optional[int] = None
    updated_ts: Optional[int] = None
    filled_ts: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DCALevel":
        if not isinstance(data, dict):
            raise TypeError("data must be a dict")

        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        clean: Dict[str, Any] = {}
        for k, v in data.items():
            if k in fields:
                clean[k] = v

        return cls(**clean)


@dataclass
class DCAStatePerSymbol:
    """Состояние DCA-кампании по одной паре.

    Хранится в файле <SYMBOL>_grid.json и используется для статусов/ролловера.
    """

    symbol: str
    tf1: str
    tf2: str

    campaign_id: str = ""
    campaign_start_ts: Optional[int] = None
    campaign_end_ts: Optional[int] = None
    closed_reason: str = ""

    config: DCAConfigPerSymbol = field(default_factory=lambda: DCAConfigPerSymbol(symbol=""))

    total_levels: int = 0
    filled_levels: int = 0
    spent_usdc: float = 0.0

    current_price: float = 0.0
    current_anchor_price: float = 0.0
    current_market_mode: str = ""

    current_atr_tf1: float = 0.0
    grid_anchor_type: str = ""
    grid_depth_abs: float = 0.0
    grid_depth_pct: float = 0.0

    current_levels: List[DCALevel] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Явно сериализуем вложенные сущности
        data["config"] = self.config.to_dict()
        data["current_levels"] = [lvl.to_dict() for lvl in self.current_levels]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DCAStatePerSymbol":
        if not isinstance(data, dict):
            raise TypeError("data must be a dict")

        # Конфиг
        raw_cfg = data.get("config") or {}
        if isinstance(raw_cfg, dict):
            config = DCAConfigPerSymbol.from_dict(raw_cfg)
        else:
            config = DCAConfigPerSymbol(symbol=str(data.get("symbol", "")).upper())

        # Уровни
        raw_levels = data.get("current_levels") or []
        levels: List[DCALevel] = []
        if isinstance(raw_levels, list):
            for item in raw_levels:
                if not isinstance(item, dict):
                    continue
                try:
                    levels.append(DCALevel.from_dict(item))
                except Exception:
                    continue

        merged: Dict[str, Any] = dict(data)
        merged["config"] = config
        merged["current_levels"] = levels

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