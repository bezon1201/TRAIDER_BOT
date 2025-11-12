# TRAIDER_BOT v6.0

## Что нового в v6.0

✨ **Планировщик метрик (metric_scheduler.py):**
- Автоматический цикл сбора метрик каждые P секунд (дефолт 3600)
- Автоматическая публикация market_mode каждые N часов (дефолт 24)
- Джитер 1-3 сек для случайного смещения
- Lock-защита от двойного запуска
- Команды управления: /scheduler on|off|confyg|period|publish
- Логирование в metric_scheduler.log и Render-лог

## Команды v6.0

- `/coins` — показать пары
- `/coins PAIR1 PAIR2` — добавить пары
- `/coins delete PAIR1 PAIR2` — удалить пары
- `/now` — собрать метрики вручную
- `/market force 12+6` — market_mode для 12+6
- `/market force 4+2` — market_mode для 4+2
- `/scheduler confyg` — показать конфиг планировщика
- `/scheduler on` — включить планировщик
- `/scheduler off` — отключить планировщик
- `/scheduler period <900-86400>` — период сбора в сек
- `/scheduler publish <1-96>` — период публикации в часах
- `/data` — список файлов
- `/data export all` — отправить все
- `/data delete all` — удалить все
- `/data delete file1.xxx, file2.xxx` — удалить конкретные

## Файлы (9)

1. main.py — v6.0 (управление планировщиком)
2. metric_scheduler.py — v6.0 (NEW планировщик)
3. metrics.py — v5.2
4. data.py — v5.2
5. collector.py — v5.1
6. indicators.py — v5.0
7. market_calculation.py — v5.4
8. requirements.txt (оригинальный)
9. README.md — v6.0

---
Version 6.0 - 12.11.2025
Планировщик активирован автоматически при старте бота
