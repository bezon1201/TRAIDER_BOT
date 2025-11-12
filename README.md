# TRAIDER_BOT v6.0

## Версия 6.0 - Планировщик без lock

### Структура
- main.py - точка входа и Telegram команды
- data.py - работа с файлами
- metrics.py - управление парами
- collector.py - сбор метрик с Binance
- indicators.py - технические индикаторы
- market_calculation.py - расчет raw market
- metric_scheduler.py - автоопланировщик (БЕЗ lock)

### Новое в v6.0
✓ Планировщик без lock-файла
✓ Автоцикл с джиттером 1-3 сек
✓ Конфиг с дефолтами: период 3600s, публик 24h
✓ Логирование всех действий в JSONL
✓ Команды /scheduler confyg|on|off|period|publish
✓ На-лету изменение параметров без перезапуска
✓ Всегда стартует в режиме on

### Команды
/start - справка
/coins - список пар
/coins PAIR1 PAIR2 - добавить
/coins delete PAIR1 PAIR2 - удалить
/now - собрать сейчас
/data - файлы
/data export all - скачать все
/scheduler confyg - конфиг
/scheduler on|off - вкл/выкл
/scheduler period <900-86400> - период
/scheduler publish <1-96> - часы публик
