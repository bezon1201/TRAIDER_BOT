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
OrderStatus = Literal["NEW", "FILLED", "CANCELED", "ACTIVE"]

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
    """Обновить типы всех NEW-ордеров (MARKET_BUY/LIMIT_BUY) по актуальной цене.

    Сетки (grid/state) не трогаются. Меняется только поле order_type у ордеров
    со статусом "NEW" в файле <SYMBOL>_orders.json.
    last_price должен быть актуальной рыночной ценой (обычно с Binance).
    """
    symbol_u = symbol.upper()
    try:
        lp = float(last_price)
    except (TypeError, ValueError):
        log.warning("refresh_order_types_from_price: некорректная цена %r для %s", last_price, symbol_u)
        return

    if lp <= 0:
        log.warning("refresh_order_types_from_price: неположительная цена %f для %s", lp, symbol_u)
        return

    orders = load_orders(symbol_u)
    if not orders:
        log.info("refresh_order_types_from_price: нет ордеров для %s", symbol_u)
        # Всё равно логируем факт ручного REFRESH в DCA-лог
        try:
            log_dca_event(symbol_u, "orders_refreshed", reason=reason)
        except Exception as e:  # noqa: BLE001
            log.exception("Не удалось записать DCA-лог (orders_refreshed) для %s: %s", symbol_u, e)
        return

    now_ts = int(time.time())
    changed = False

    for o in orders:
        status = getattr(o, "status", "NEW") or "NEW"
        if status != "NEW":
            continue

        try:
            price = float(getattr(o, "price", 0.0) or 0.0)
        except (TypeError, ValueError):
            log.warning("refresh_order_types_from_price: некорректная цена ордера %r для %s", o, symbol_u)
            continue

        # last_price > 0 и price >= last_price → MARKET_BUY, иначе LIMIT_BUY
        if price >= lp:
            new_type: OrderType = "MARKET_BUY"
        else:
            new_type = "LIMIT_BUY"

        if getattr(o, "order_type", None) != new_type:
            o.order_type = new_type
            o.updated_ts = now_ts
            changed = True

    if changed:
        save_orders(symbol_u, orders)
        log.info("refresh_order_types_from_price: обновлены типы ордеров для %s", symbol_u)

    # Фиксируем факт ручного REFRESH в общем DCA-логе (без статистики).
    try:
        log_dca_event(symbol_u, "orders_refreshed", reason=reason)
    except Exception as e:  # noqa: BLE001
        log.exception("Не удалось записать DCA-лог (orders_refreshed) для %s: %s", symbol_u, e)

# ---- Engine helpers for virtual execution of individual orders ----

def _grid_file_path(symbol: str) -> str:
    """Путь к файлу <SYMBOL>_grid.json для прямой работы с сеткой.

    Используем STORAGE_DIR, чтобы не тянуть dca_grid и не создавать циклический импорт.
    """
    symbol_u = (symbol or "").upper()
    filename = f"{symbol_u}_grid.json"
    return os.path.join(STORAGE_DIR, filename)


def _mark_level_filled_in_grid(
    symbol: str,
    grid_id: int,
    level_index: int,
    *,
    filled: bool,
    filled_ts: Optional[float] = None,
) -> None:
    """Отметить уровень сетки как filled/unfilled в <SYMBOL>_grid.json.

    Мы обновляем только поля filled/filled_ts для нужного уровня в current_levels.
    Агрегаторы (filled_levels, remaining_levels, spent_usdc, avg_price и т.п.)
    на этом этапе не пересчитываем — это будет сделано отдельным шагом.
    """
    path = _grid_file_path(symbol)
    if not os.path.exists(path):
        log.info(
            "mark_level_filled_in_grid: файл сетки %s не найден для %s",
            path,
            symbol,
        )
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        log.exception(
            "mark_level_filled_in_grid: не удалось прочитать %s: %s",
            path,
            e,
        )
        return

    levels = data.get("current_levels")
    if not isinstance(levels, list):
        log.warning(
            "mark_level_filled_in_grid: в %s нет корректного current_levels",
            path,
        )
        return

    changed = False
    for lvl in levels:
        try:
            lvl_grid_id = int(lvl.get("grid_id") or 0)
            lvl_index = int(lvl.get("level_index") or 0)
        except Exception:
            continue

        if lvl_grid_id == int(grid_id) and lvl_index == int(level_index):
            lvl["filled"] = bool(filled)
            lvl["filled_ts"] = int(filled_ts) if filled_ts is not None else None
            changed = True
            break

    if not changed:
        log.warning(
            "mark_level_filled_in_grid: уровень grid_id=%s, level_index=%s не найден в %s",
            grid_id,
            level_index,
            path,
        )
        return

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        log.exception(
            "mark_level_filled_in_grid: не удалось сохранить %s: %s",
            path,
            e,
        )


def execute_virtual_market_buy(
    symbol: str,
    grid_id: int,
    level_index: int,
    *,
    execution_price: float,
    commission: float = 0.0,
    commission_asset: Optional[str] = None,
    reason: ReasonType = "manual",
) -> Optional[VirtualOrder]:
    """Полностью исполнить BUY-ордер по рыночной цене (виртуально).

    Меняет только:
    - статус ордера (NEW/CANCELED -> FILLED),
    - поля исполнения (avg_fill_price, filled_qty, filled_quote_qty, commission),
    - помечает уровень в сетке как filled,
    - пишет запись order_filled в DCA-лог.

    Частичных исполнений не делаем — ордер либо полностью исполнен, либо нет.
    """
    symbol_u = (symbol or "").upper()
    try:
        price = float(execution_price)
    except (TypeError, ValueError):
        log.warning(
            "execute_virtual_market_buy: некорректная цена исполнения %r для %s",
            execution_price,
            symbol_u,
        )
        return None

    if price <= 0:
        log.warning(
            "execute_virtual_market_buy: неположительная цена исполнения %f для %s",
            price,
            symbol_u,
        )
        return None

    orders = load_orders(symbol_u)
    if not orders:
        log.info("execute_virtual_market_buy: нет ордеров для %s", symbol_u)
        return None

    target: Optional[VirtualOrder] = None
    for o in orders:
        if getattr(o, "grid_id", None) == grid_id and getattr(o, "level_index", None) == level_index:
            target = o
            break

    if not target:
        log.warning(
            "execute_virtual_market_buy: ордер grid_id=%s level_index=%s для %s не найден",
            grid_id,
            level_index,
            symbol_u,
        )
        return None

    status = getattr(target, "status", "NEW") or "NEW"
    if status in ("FILLED", "ACTIVE"):
        log.info(
            "execute_virtual_market_buy: ордер уже в статусе %s для %s (grid_id=%s, level_index=%s)",
            status,
            symbol_u,
            grid_id,
            level_index,
        )
        return None

    if status not in ("NEW", "CANCELED"):
        log.warning(
            "execute_virtual_market_buy: неподдерживаемый статус %s для %s (grid_id=%s, level_index=%s)",
            status,
            symbol_u,
            grid_id,
            level_index,
        )
        return None

    side = getattr(target, "side", "BUY")
    if side != "BUY":
        log.warning(
            "execute_virtual_market_buy: ордер не BUY (%s) для %s (grid_id=%s, level_index=%s)",
            side,
            symbol_u,
            grid_id,
            level_index,
        )
        return None

    # Плановый объём
    try:
        planned_quote = float(getattr(target, "quote_qty", 0.0) or 0.0)
    except (TypeError, ValueError):
        planned_quote = 0.0

    try:
        planned_qty = float(getattr(target, "qty", 0.0) or 0.0)
    except (TypeError, ValueError):
        planned_qty = 0.0

    if planned_quote <= 0 and planned_qty > 0 and price > 0:
        planned_quote = planned_qty * price
    elif planned_qty <= 0 and planned_quote > 0 and price > 0:
        planned_qty = planned_quote / price

    if planned_quote <= 0 or planned_qty <= 0:
        log.warning(
            "execute_virtual_market_buy: некорректный объём ордера (qty=%s, quote=%s) для %s",
            planned_qty,
            planned_quote,
            symbol_u,
        )
        return None

    filled_quote = planned_quote
    filled_qty = filled_quote / price if price > 0 else planned_qty

    now_ts = time.time()

    target.status = "FILLED"
    target.avg_fill_price = price
    target.filled_qty = filled_qty
    target.filled_quote_qty = filled_quote
    target.filled_ts = now_ts
    target.updated_ts = now_ts

    if commission and commission > 0:
        try:
            target.commission = float(commission)
        except (TypeError, ValueError):
            target.commission = 0.0
        target.commission_asset = commission_asset

    save_orders(symbol_u, orders)

    # Помечаем уровень в сетке как filled (агрегаторы пока не трогаем).
    try:
        _mark_level_filled_in_grid(
            symbol_u,
            grid_id,
            level_index,
            filled=True,
            filled_ts=now_ts,
        )
    except Exception as e:  # noqa: BLE001
        log.exception(
            "execute_virtual_market_buy: не удалось обновить сетку для %s (grid_id=%s, level_index=%s): %s",
            symbol_u,
            grid_id,
            level_index,
            e,
        )

    try:
        log_dca_event(
            symbol_u,
            "order_filled",
            grid_id=grid_id,
            reason=reason,
            order_id=getattr(target, "order_id", None),
            level_index=getattr(target, "level_index", None),
            order_type=getattr(target, "order_type", None),
            level_price=getattr(target, "price", None),
            execution_price=price,
            qty=filled_qty,
            quote_qty=filled_quote,
            commission=getattr(target, "commission", 0.0),
            commission_asset=getattr(target, "commission_asset", None),
        )
    except Exception as e:  # noqa: BLE001
        log.exception(
            "execute_virtual_market_buy: не удалось записать DCA-лог для %s: %s",
            symbol_u,
            e,
        )

    return target


def activate_virtual_limit_buy(
    symbol: str,
    grid_id: int,
    level_index: int,
    *,
    reason: ReasonType = "manual",
) -> Optional[VirtualOrder]:
    """Активировать лимитный BUY-ордер (виртуально отправить лимитку).

    Меняет только статус ордера:
    - NEW/CANCELED -> ACTIVE

    Сетка (grid.json) на этом этапе не изменяется — уровень остаётся с filled=False.
    """
    symbol_u = (symbol or "").upper()
    orders = load_orders(symbol_u)
    if not orders:
        log.info("activate_virtual_limit_buy: нет ордеров для %s", symbol_u)
        return None

    target: Optional[VirtualOrder] = None
    for o in orders:
        if getattr(o, "grid_id", None) == grid_id and getattr(o, "level_index", None) == level_index:
            target = o
            break

    if not target:
        log.warning(
            "activate_virtual_limit_buy: ордер grid_id=%s level_index=%s для %s не найден",
            grid_id,
            level_index,
            symbol_u,
        )
        return None

    status = getattr(target, "status", "NEW") or "NEW"
    if status in ("FILLED", "ACTIVE"):
        log.info(
            "activate_virtual_limit_buy: ордер уже в статусе %s для %s (grid_id=%s, level_index=%s)",
            status,
            symbol_u,
            grid_id,
            level_index,
        )
        return None

    if status not in ("NEW", "CANCELED"):
        log.warning(
            "activate_virtual_limit_buy: неподдерживаемый статус %s для %s (grid_id=%s, level_index=%s)",
            status,
            symbol_u,
            grid_id,
            level_index,
        )
        return None

    side = getattr(target, "side", "BUY")
    if side != "BUY":
        log.warning(
            "activate_virtual_limit_buy: ордер не BUY (%s) для %s (grid_id=%s, level_index=%s)",
            side,
            symbol_u,
            grid_id,
            level_index,
        )
        return None

    order_type = getattr(target, "order_type", "LIMIT_BUY") or "LIMIT_BUY"
    if order_type != "LIMIT_BUY":
        log.warning(
            "activate_virtual_limit_buy: неподдерживаемый order_type %s для %s (grid_id=%s, level_index=%s)",
            order_type,
            symbol_u,
            grid_id,
            level_index,
        )
        return None

    now_ts = time.time()
    target.status = "ACTIVE"

    # Если created_ts пустой/кривой — ставим текущее время
    try:
        created_ts = float(getattr(target, "created_ts", 0.0) or 0.0)
    except (TypeError, ValueError):
        created_ts = 0.0
    if created_ts <= 0:
        target.created_ts = now_ts
    target.updated_ts = now_ts

    try:
        save_orders(symbol_u, orders)
    except Exception as e:  # noqa: BLE001
        log.exception(
            "activate_virtual_limit_buy: не удалось сохранить ордера для %s: %s",
            symbol_u,
            e,
        )
        return None

    try:
        log_dca_event(
            symbol_u,
            "order_placed",
            reason=reason,
            grid_id=grid_id,
            order_id=getattr(target, "order_id", None),
            level_index=getattr(target, "level_index", None),
            order_type=getattr(target, "order_type", None),
            price=getattr(target, "price", None),
            qty=getattr(target, "qty", None),
            quote_qty=getattr(target, "quote_qty", None),
        )
    except Exception as e:  # noqa: BLE001
        log.exception(
            "activate_virtual_limit_buy: не удалось записать DCA-лог для %s: %s",
            symbol_u,
            e,
        )

    return target

