# TRAIDER_BOT v5.4

## Что нового в v5.4

✨ **Команда /market force:**
- `/market force 12+6` — расчет market_mode из raw_market_12+6.jsonl
- `/market force 4+2` — расчет market_mode из raw_market_4+2.jsonl
- Голосование >60% за один из режимов (UP/DOWN/RANGE)
- Запись в <symbol>.json

## Команды v5.4

- `/coins` — показать пары
- `/coins PAIR1 PAIR2` — добавить пары
- `/coins delete PAIR1 PAIR2` — удалить пары
- `/now` — собрать метрики
- `/market force 12+6` — market_mode для 12+6 ✨ NEW
- `/market force 4+2` — market_mode для 4+2 ✨ NEW
- `/data` — список файлов
- `/data delete file1.xxx, file2.xxx` — удалить конкретные
- `/data export all` — отправить все
- `/data delete all` — удалить все

## Файлы (8)

1. main.py — v5.4
2. metrics.py — v5.2
3. data.py — v5.2
4. collector.py — v5.1
5. indicators.py — v5.0
6. market_calculation.py — v5.4 (с force_market_mode)
7. requirements.txt
8. README.md

---
Version 5.4 - 12.11.2025
