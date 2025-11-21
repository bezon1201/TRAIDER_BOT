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



def _grid_file_path(symbol: str) -> str:
    """Путь к файлу <SYMBOL>_grid.json.

    Отдельный хелпер, чтобы dca_orders мог аккуратно обновлять флаги filled
    для уровней без жёсткой зависимости от dca_grid/dca_storage.
    """
    symbol_u = (symbol or "").upper()
    return os.path.join(STORAGE_DIR, f"{symbol_u}_grid.json")


def _mark_level_filled_in_grid(
    symbol: str,
    grid_id: int,
    level_index: int,
    *,
    filled: bool,
    filled_ts: Optional[int] = None,
) -> None:
    """Обновить флаги filled/filled_ts для уровня в <SYMBOL>_grid.json.

    Функция старается быть максимально аккуратной:
    - если файл сетки отсутствует или повреждён — просто логирует предупреждение;
    - если нужный уровень не найден — тоже только лог.
    Ошибки не пробрасываются наружу, чтобы не ломать основную логику исполнения
    ордеров.
    """
    path = _grid_file_path(symbol)
    if not os.path.exists(path):
        log.warning(
            "grid.json для %s не найден при попытке обновить filled для grid_id=%s, level_index=%s",
            symbol,
            grid_id,
            level_index,
        )
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        log.warning(
            "Не удалось прочитать grid.json для %s при обновлении filled (grid_id=%s, level_index=%s)",
            symbol,
            grid_id,
            level_index,
        )
        return

    if not isinstance(raw, dict):
        log.warning(
            "Некорректная структура grid.json для %s при обновлении filled (ожидался dict)",
            symbol,
        )
        return

    def _update_levels(levels_obj: Any) -> bool:
        updated_local = False
        if not isinstance(levels_obj, list):
            return False
        for lvl in levels_obj:
            if not isinstance(lvl, dict):
                continue
            try:
                gid = int(lvl.get("grid_id") or 1)
                idx = int(lvl.get("level_index") or 0)
            except Exception:
                continue
            if gid == grid_id and idx == level_index:
                lvl["filled"] = bool(filled)
                lvl["filled_ts"] = filled_ts
                updated_local = True
        return updated_local

    updated = False
    # Основной список уровней
    if "levels" in raw:
        if _update_levels(raw.get("levels")):
            updated = True
    # Текущая сетка (current_levels)
    if "current_levels" in raw:
        if _update_levels(raw.get("current_levels")):
            updated = True

    if not updated:
        log.warning(
            "Не найден уровень в grid.json для %s (grid_id=%s, level_index=%s) при обновлении filled",
            symbol,
            grid_id,
            level_index,
        )
        return

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
    except OSError as e:  # noqa: BLE001
        log.exception(
            "Не удалось сохранить grid.json для %s после обновления filled (grid_id=%s, level_index=%s): %s",
            symbol,
            grid_id,
            level_index,
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
    """Виртуально исполнить BUY-ордер по рыночной цене.

    Функция:
    - ищет ордер по (symbol, grid_id, level_index) в <SYMBOL>_orders.json;
    - допускает запуск только для статусов NEW и CANCELED;
    - считает полное исполнение (partial fill не поддерживается);
    - обновляет поля filled_*, avg_fill_price, status, filled_ts/updated_ts;
    - по возможности записывает commission/commission_asset;
    - помечает соответствующий уровень в <SYMBOL>_grid.json как filled=True;
    - пишет событие в DCA-лог (event="order_filled").
    """
    symbol_u = (symbol or "").upper()
    try:
        px = float(execution_price)
    except (TypeError, ValueError):
        log.warning(
            "execute_virtual_market_buy: некорректная цена исполнения %r для %s",
            execution_price,
            symbol_u,
        )
        return None

    if px <= 0:
        log.warning(
            "execute_virtual_market_buy: неположительная цена исполнения %f для %s",
            px,
            symbol_u,
        )
        return None

    orders = load_orders(symbol_u)
    if not orders:
        log.warning(
            "execute_virtual_market_buy: нет ордеров для %s при попытке исполнить grid_id=%s, level_index=%s",
            symbol_u,
            grid_id,
            level_index,
        )
        return None

    target: Optional[VirtualOrder] = None
    for o in orders:
        if o.grid_id == grid_id and o.level_index == level_index:
            target = o
            break

    if target is None:
        log.warning(
            "execute_virtual_market_buy: ордер не найден для %s (grid_id=%s, level_index=%s)",
            symbol_u,
            grid_id,
            level_index,
        )
        return None

    status = getattr(target, "status", "NEW") or "NEW"
    if status not in ("NEW", "CANCELED"):
        log.info(
            "execute_virtual_market_buy: ордер %s для %s уже в статусе %s, исполнение пропущено",
            target.order_id,
            symbol_u,
            status,
        )
        return None

    if target.side != "BUY":
        log.warning(
            "execute_virtual_market_buy: попытка исполнить не-BUY ордер %s для %s",
            target.order_id,
            symbol_u,
        )
        return None

    # Плановый notional и количество.
    try:
        planned_quote = float(target.quote_qty or 0.0)
    except (TypeError, ValueError):
        planned_quote = 0.0

    try:
        planned_qty = float(target.qty or 0.0)
    except (TypeError, ValueError):
        planned_qty = 0.0

    if planned_quote <= 0 and planned_qty > 0:
        planned_quote = planned_qty * px

    if planned_quote <= 0 and planned_qty <= 0:
        log.warning(
            "execute_virtual_market_buy: у ордера %s для %s нет валидного объёма (qty/quote_qty)",
            target.order_id,
            symbol_u,
        )
        return None

    filled_quote_qty = planned_quote
    filled_qty = filled_quote_qty / px if px > 0 else 0.0

    now_ts = time.time()
    target.status = "FILLED"
    target.avg_fill_price = px
    target.filled_qty = filled_qty
    target.filled_quote_qty = filled_quote_qty
    target.filled_ts = now_ts
    target.updated_ts = now_ts

    if commission and commission > 0:
        try:
            target.commission = float(commission)
        except (TypeError, ValueError):
            target.commission = 0.0
        target.commission_asset = commission_asset
    else:
        # Комиссию явно не задали — оставляем поля по умолчанию.
        pass

    # Сохраняем обновлённый список ордеров.
    try:
        # Перезаписываем объект в списке (target уже является элементом списка).
        save_orders(symbol_u, orders)
    except Exception as e:  # noqa: BLE001
        log.exception(
            "execute_virtual_market_buy: не удалось сохранить ордера для %s: %s",
            symbol_u,
            e,
        )
        return None

    # Помечаем уровень в grid.json как filled.
    try:
        _mark_level_filled_in_grid(
            symbol_u,
            grid_id,
            level_index,
            filled=True,
            filled_ts=int(now_ts),
        )
    except Exception as e:  # noqa: BLE001
        # Не считаем это фатальной ошибкой для исполнения ордера.
        log.exception(
            "execute_virtual_market_buy: ошибка при обновлении grid.json для %s: %s",
            symbol_u,
            e,
        )

    # Пишем событие в DCA-лог.
    try:
        log_dca_event(
            symbol_u,
            "order_filled",
            grid_id=grid_id,
            reason=reason,
            order_id=target.order_id,
            level_index=target.level_index,
            order_type=target.order_type,
            price=px,
            qty=filled_qty,
            quote_qty=filled_quote_qty,
            commission=target.commission,
            commission_asset=target.commission_asset,
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
    """Перевести LIMIT BUY ордер в статус ACTIVE без исполнения.

    Функция:
    - ищет ордер по (symbol, grid_id, level_index) в <SYMBOL>_orders.json;
    - допускает запуск только для статусов NEW и CANCELED;
    - проверяет, что order_type == "LIMIT_BUY";
    - выставляет status="ACTIVE" и updated_ts/created_ts при необходимости;
    - НЕ трогает <SYMBOL>_grid.json (filled остаётся False);
    - пишет событие в DCA-лог (event="order_placed").
    """
    symbol_u = (symbol or "").upper()
    orders = load_orders(symbol_u)
    if not orders:
        log.warning(
            "activate_virtual_limit_buy: нет ордеров для %s при попытке активировать grid_id=%s, level_index=%s",
            symbol_u,
            grid_id,
            level_index,
        )
        return None

    target: Optional[VirtualOrder] = None
    for o in orders:
        if o.grid_id == grid_id and o.level_index == level_index:
            target = o
            break

    if target is None:
        log.warning(
            "activate_virtual_limit_buy: ордер не найден для %s (grid_id=%s, level_index=%s)",
            symbol_u,
            grid_id,
            level_index,
        )
        return None

    status = getattr(target, "status", "NEW") or "NEW"
    if status not in ("NEW", "CANCELED"):
        log.info(
            "activate_virtual_limit_buy: ордер %s для %s уже в статусе %s, активировать нельзя",
            target.order_id,
            symbol_u,
            status,
        )
        return None

    if target.side != "BUY":
        log.warning(
            "activate_virtual_limit_buy: попытка активировать не-BUY ордер %s для %s",
            target.order_id,
            symbol_u,
        )
        return None

    if target.order_type != "LIMIT_BUY":
        log.warning(
            "activate_virtual_limit_buy: попытка активировать не-LIMIT ордер %s для %s (order_type=%s)",
            target.order_id,
            symbol_u,
            target.order_type,
        )
        return None

    now_ts = time.time()
    target.status = "ACTIVE"
    if not getattr(target, "created_ts", None):
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

    # Записываем событие в DCA-лог.
    try:
        log_dca_event(
            symbol_u,
            "order_placed",
            grid_id=grid_id,
            reason=reason,
            order_id=target.order_id,
            level_index=target.level_index,
            order_type=target.order_type,
            price=target.price,
            qty=target.qty,
            quote_qty=target.quote_qty,
        )
    except Exception as e:  # noqa: BLE001
        log.exception(
            "activate_virtual_limit_buy: не удалось записать DCA-лог для %s: %s",
            symbol_u,
            e,
        )

    return target
