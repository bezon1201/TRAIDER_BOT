# TRAIDER_BOT v5.2

## Что нового

### ✅ Расширенное управление списком пар
- `/coins` — показать список активных пар
- `/coins <symbol> ...` — добавить пары в список
- `/coins delete <symbol> ...` — удалить пары из списка

### ✅ Raw режимы (из v5.1)
Автоматический расчет и сохранение:
- `<SYMBOL>_raw_market_12+6.jsonl`
- `<SYMBOL>_raw_market_4+2.jsonl`

### ✅ Архитектура v5.0
- 30 свечей вместо 100
- История SMA14/ATR14 для расчетов
- Все фильтры сохраняются

## Команды

- `/start` — справка
- `/coins` — показать пары
- `/coins PAIR1 PAIR2` — добавить пары
- `/coins delete PAIR1 PAIR2` — удалить пары
- `/now` — собрать метрики + расчитать raw
- `/data` — список файлов
- `/data export all` — отправить все
- `/data delete all` — удалить все

## Пример использования

```
/coins BTCUSDT ETHUSDT BNBUSDT    # Добавить 3 пары
/coins                             # Показать пары
/coins delete BNBUSDT              # Удалить пару (например, опечатка)
/now                               # Собрать метрики
```

## Файлы (по паре)

```
<SYMBOL>.json               — основные метрики
<SYMBOL>_raw_market_12+6.jsonl  — raw сигналы (12h+6h)
<SYMBOL>_raw_market_4+2.jsonl   — raw сигналы (4h+2h)
```

---
Version 5.2 - 12.11.2025
