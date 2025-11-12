# TRAIDER_BOT v6.0

## Структура
- main.py - точка входа
- data.py - работа с файлами
- metrics.py - парсинг команд /coins
- collector.py - сбор метрик
- indicators.py - технические индикаторы
- market_calculation.py - расчет raw market
- metric_scheduler.py - фоновый планировщик
- requirements.txt - зависимости

## Команды
/start - справка
/coins - показать пары
/coins PAIR1 PAIR2 - добавить пары
/now - собрать метрики сейчас
/data - список файлов
/scheduler confyg - конфиг планировщика
/scheduler on|off - вкл/выкл планировщик
/scheduler period 900 - период сбора
/scheduler publish 12 - период публикации

## Развертывание
1. git clone ...
2. Настроить переменные окружения
3. git push к Render
