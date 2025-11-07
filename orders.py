"""
orders.py — логика ордеров для бота (v17)

В ЭТОЙ ВЕРСИИ:
- Файл пока содержит только структуры данных и вспомогательные функции.
- Весь существующий рабочий код из app.py *НЕ* тронут.
- В следующих версиях будем переносить обработчики ORDERS_* из app.py сюда.

Идея:
  app.py отвечает только за Telegram / FastAPI.
  orders.py отвечает за расчёты: виртуальные ордера, бюджеты, статусы.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Dict, Any


Side = Literal["BUY", "SELL"]


@dataclass
class BudgetPosition:
    """
    Срез бюджета по конкретному символу и месяцу.
    Сейчас это просто оболочка над dict, чтобы было удобнее работать.
    """
    free: float
    reserve: float
    spent: float

    @property
    def total(self) -> float:
        return self.free + self.reserve + self.spent


@dataclass
class VirtualOrder:
    """
    Виртуальный ордер, который мы показываем в карточке перед отправкой на биржу.

    Пока что здесь только минимально необходимое; по мере переноса логики
    из app.py можно будет расширять (стопы, тейки и т.п.).
    """
    symbol: str
    side: Side
    price: float
    amount: float
    comment: str | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "price": self.price,
            "amount": self.amount,
            "comment": self.comment,
        }


def calc_order_notional(price: float, amount: float) -> float:
    """
    Общая стоимость (quote-часть) ордера.
    """
    return round(price * amount, 8)


def allocate_from_budget(
    free: float,
    reserve: float,
    spent: float,
    need: float,
) -> tuple[bool, float, float, float]:
    """
    Простая функция, которая пытается "занять" деньги из бюджета.

    Возвращает:
      ok, new_free, new_reserve, new_spent

    Логика сейчас примитивная: сначала тратим free, потом, если нужно,
    залезаем в reserve. Реальные правила можно будет донастроить позже.
    """
    if need <= 0:
        return True, free, reserve, spent

    total_available = free + reserve
    if need > total_available:
        return False, free, reserve, spent

    use_free = min(free, need)
    remaining = need - use_free
    use_reserve = remaining

    new_free = free - use_free
    new_reserve = reserve - use_reserve
    new_spent = spent + need

    return True, new_free, new_reserve, new_spent


# Заготовка под будущий перенос логики из app.py.
# Пример того, как может выглядеть "чистая" функция для сборки
# превью по LIMIT 0:
def build_limit0_preview(
    symbol: str,
    side: Side,
    price: float,
    amount: float,
    month: str,
    budget_snapshot: BudgetPosition,
) -> dict:
    """
    Чистая функция без Telegram: готовит данные для карточки LIMIT 0.

    Возвращает словарь с тем, что нужно отобразить в UI.
    """
    notional = calc_order_notional(price, amount)
    ok, new_free, new_reserve, new_spent = allocate_from_budget(
        budget_snapshot.free,
        budget_snapshot.reserve,
        budget_snapshot.spent,
        notional,
    )

    return {
        "ok": ok,
        "symbol": symbol,
        "side": side,
        "price": price,
        "amount": amount,
        "notional": notional,
        "month": month,
        "budget_before": {
            "free": budget_snapshot.free,
            "reserve": budget_snapshot.reserve,
            "spent": budget_snapshot.spent,
        },
        "budget_after": {
            "free": new_free,
            "reserve": new_reserve,
            "spent": new_spent,
        },
    }
