# TRAIDER_BOT v5.3

## Что нового в v5.3

✨ **Расширенное управление файлами:**
- `/data delete file1.xxx, file2.xxx` — удаление конкретных файлов
- `/data import` — подготовка к импорту

## Команды v5.3

- `/start` — справка
- `/coins` — показать пары
- `/coins PAIR1 PAIR2` — добавить пары
- `/coins delete PAIR1 PAIR2` — удалить пары
- `/now` — собрать метрики
- `/data` — список файлов
- `/data delete file1.xxx, file2.xxx` — удалить конкретные ✨ NEW
- `/data export all` — отправить все
- `/data delete all` — удалить все
- `/data import` — импортировать ✨ NEW

## Файлы

```
main.py             — FastAPI bot (v5.3)
metrics.py          — управление парами
data.py             — работа с файлами
collector.py        — сбор с Binance
indicators.py       — SMA14 + ATR14
market_calculation.py — raw режимы
requirements.txt    — зависимости
README.md           — документация
```

---
Version 5.3 - 12.11.2025
