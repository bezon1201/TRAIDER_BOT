from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional, Literal, Any

import logging

from config import STORAGE_DIR
from dca_log import log_dca_event, ReasonType

OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["MARKET_BUY", "LIMIT_BUY"]
OrderStatus = Literal["NEW", "FILLED", "CANCELED"]

log = logging.getLogger(__name__)


@dataclass
class VirtualOrder:
    """Виртуальный ордер DCA, максимально приближенный к реальному биржевому ордеру."""

    # Идентификация
    order_id: str          # внутренний уникальный ID ордера
    symbol: str            # тикер, например "ETHUSDT"
    grid_id: int           # ID сетки, к которой относится ордер
    level_index: int       # индекс уровня в сетке (1..N)

    # Основные торговые параметры (задуманный уровень)
    side: OrderSide        # "BUY" или "SELL" (сейчас используем только "BUY")
    order_type: OrderType  # "MARKET_BUY" или "LIMIT_BUY"
    price: float           # цена уровня сетки / лимит
    qty: float             # количество базовой монеты, запланированное для покупки
    quote_qty: float       # notional в USDC по уровню (план)

    # Статус и таймстемпы
    status: OrderStatus    # "NEW", "FILLED", "CANCELED"
    created_ts: float      # время создания ордера (epoch seconds)
    updated_ts: float      # время последнего изменения ордера

    filled_ts: Optional[float] = None    # время полного заполнения
    canceled_ts: Optional[float] = None  # время отмены

    # Фактическое исполнение (виртуальное или реальное)
    avg_fill_price: Optional[float] = None   # средняя цена исполнения
    filled_qty: float = 0.0                  # реально купленное количество (base)
    filled_quote_qty: float = 0.0            # реально потраченный notional (USDC)

    # Комиссия при исполнении
    commission: float = 0.0                  # суммарная комиссия
    commission_asset: Optional[str] = None   # в какой монете комиссия (BNB, USDC и т.п.)

    # Поля под реальную биржу (на будущее)
    exchange_order_id: Optional[str] = None  # orderId с биржи
    client_order_id: Optional[str] = None    # clientOrderId с биржи

    def to_dict(self) -> dict:
        """Сериализация в dict для сохранения в JSON."""
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "VirtualOrder":
        """Восстановление VirtualOrder из dict."""
        return VirtualOrder(**data)


def _orders_path(symbol: str) -> str:
    """Путь к файлу с ордерами для заданного символа. Пример: STORAGE_DIR/ETHUSDT_orders.json"""
    filename = f"{symbol.upper()}_orders.json"
    return os.path.join(STORAGE_DIR, filename)


def load_orders(symbol: str) -> List[VirtualOrder]:
    """Загрузить все виртуальные ордера для символа. Если файл не существует — вернуть пустой список."""
    path = _orders_path(symbol)
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        # В случае проблем с чтением/JSON считаем, что ордеров нет
        log.warning("Не удалось прочитать файл ордеров для %s", symbol)
        return []

    orders_data = raw.get("orders", [])
    orders: List[VirtualOrder] = []
    for item in orders_data:
        try:
            orders.append(VirtualOrder.from_dict(item))
        except TypeError:
            # Если структура повреждена, пропускаем конкретный элемент
            log.warning("Пропускаем повреждённый ордер в файле %s_orders.json", symbol)
            continue

    return orders


def save_orders(symbol: str, orders: List[VirtualOrder]) -> None:
    """Сохранить список виртуальных ордеров для символа в JSON-файл (полностью перезаписывает файл)."""
    path = _orders_path(symbol)
    payload = {
        "symbol": symbol.upper(),
        "orders": [o.to_dict() for o in orders],
    }

    os.makedirs(STORAGE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def make_order_id(symbol: str, grid_id: int, level_index: int, created_ts: Optional[float] = None) -> str:
    """Сгенерировать уникальный order_id.

    Формат:
        {SYMBOL}-{GRID_ID:04d}-{LEVEL_INDEX:03d}-{YYYYMMDDHHMMSS}

    Пример:
        ETHUSDT-0001-003-20251121123045
    """
    if created_ts is None:
        created_ts = time.time()

    dt = datetime.utcfromtimestamp(created_ts)
    ts_str = dt.strftime("%Y%m%d%H%M%S")
    symbol_up = symbol.upper()

    return f"{symbol_up}-{grid_id:04d}-{level_index:03d}-{ts_str}"


def create_virtual_orders_for_grid(
    symbol: str,
    grid_state: Any,
    last_price: float,
    *,
    reason: ReasonType = "manual",
) -> None:
    """Создать виртуальные ордера по уровням сетки и сохранить их в файл.

    Ожидается, что grid_state — это dict из _build_grid_for_symbol, содержащий:
    - current_grid_id
    - current_levels: список уровней с полями level_index, price, qty, notional

    last_price — текущая рыночная цена на момент построения сетки (из coin_state).
    reason — источник события: "manual" или "scheduler".
    """
    if not isinstance(grid_state, dict):
        log.warning("create_virtual_orders_for_grid ожидает dict, получено %r", type(grid_state))
        return

    levels = grid_state.get("current_levels") or []
    if not levels:
        # Нечего строить
        return

    try:
        grid_id = int(grid_state.get("current_grid_id") or 1)
    except Exception:
        grid_id = 1

    symbol_u = (symbol or "").upper()
    existing_orders = load_orders(symbol_u)
    existing_keys = {(o.grid_id, o.level_index) for o in existing_orders}

    new_orders: List[VirtualOrder] = []
    now_ts = time.time()

    for level in levels:
        try:
            level_index = int(level.get("level_index") or 0)
        except Exception:
            continue
        if level_index <= 0:
            continue

        key = (grid_id, level_index)
        if key in existing_keys:
            # На всякий случай не создаём дубликаты
            continue

        try:
            price = float(level.get("price") or 0.0)
        except Exception:
            price = 0.0
        try:
            qty = float(level.get("qty") or 0.0)
        except Exception:
            qty = 0.0
        try:
            notional = float(level.get("notional") or 0.0)
        except Exception:
            notional = 0.0

        if price <= 0 or qty <= 0 or notional <= 0:
            # Неполноценный уровень
            continue

        if last_price > 0 and price >= last_price:
            order_type: OrderType = "MARKET_BUY"
        else:
            order_type = "LIMIT_BUY"

        created_ts = now_ts
        order_id = make_order_id(symbol_u, grid_id, level_index, created_ts)

        vo = VirtualOrder(
            order_id=order_id,
            symbol=symbol_u,
            grid_id=grid_id,
            level_index=level_index,
            side="BUY",
            order_type=order_type,
            price=price,
            qty=qty,
            quote_qty=notional,
            status="NEW",
            created_ts=created_ts,
            updated_ts=created_ts,
        )
        new_orders.append(vo)

    if not new_orders:
        return

    all_orders = existing_orders + new_orders
    save_orders(symbol_u, all_orders)
    log.info("Создано %d виртуальных ордеров для %s (grid_id=%s)", len(new_orders), symbol_u, grid_id)

    # Логируем событие orders_created в общий DCA-лог.
    try:
        log_dca_event(
            symbol_u,
            "orders_created",
            grid_id=grid_id,
            reason=reason,
            count=len(new_orders),
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Не удалось записать DCA-лог (orders_created) для %s: %s", symbol_u, e)


def refresh_order_types_from_price(
    symbol: str,
    last_price: float,
    *,
    reason: ReasonType = "manual",
) -> None:
    """Обновить типы всех NEW-ордеров (MARKET_BUY/LIMIT_BUY) для символа по актуальной цене.

    Сетки (уровни) не пересчитываются, изменяется только поле order_type у ордеров
    со статусом "NEW". Цена должна приходить снаружи (обычно — с Binance).
    """
    symbol_u = (symbol or "").upper().strip()
    if not symbol_u:
        return

    try:
        lp = float(last_price)
    except (TypeError, ValueError):
        log.warning("refresh_order_types_from_price: некорректная цена %r для %s", last_price, symbol_u)
        return

    if lp <= 0:
        log.warning("refresh_order_types_from_price: неположительная цена %s для %s", lp, symbol_u)
        return

    try:
        orders = load_orders(symbol_u)
    except Exception as e:  # noqa: BLE001
        log.warning("refresh_order_types_from_price: не удалось загрузить ордера для %s: %s", symbol_u, e)
        return

    if not orders:
        # Нечего обновлять
        try:
            log_dca_event(
                symbol_u,
                "orders_refreshed",
                reason=reason,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Не удалось записать DCA-лог (orders_refreshed, empty) для %s: %s", symbol_u, e)
        return

    now_ts = time.time()
    changed = False

    for o in orders:
        # Меняем только ордера в статусе NEW
        if getattr(o, "status", None) != "NEW":
            continue

        try:
            price = float(getattr(o, "price", 0.0))
        except (TypeError, ValueError):
            price = 0.0

        if lp > 0 and price >= lp:
            new_type: OrderType = "MARKET_BUY"
        else:
            new_type = "LIMIT_BUY"

        if getattr(o, "order_type", None) != new_type:
            o.order_type = new_type  # type: ignore[assignment]
            o.updated_ts = now_ts
            changed = True

    if changed:
        try:
            save_orders(symbol_u, orders)
        except Exception as e:  # noqa: BLE001
            log.exception("Не удалось сохранить обновлённые ордера для %s: %s", symbol_u, e)
            return

    # В любом случае фиксируем факт ручного REFRESH в DCA-логе
    try:
        log_dca_event(
            symbol_u,
            "orders_refreshed",
            reason=reason,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Не удалось записать DCA-лог (orders_refreshed) для %s: %s", symbol_u, e)

