# TRAIDER_BOT v4.7 - CLEAN BUILD

## Что это

**ЧИСТАЯ сборка нового бота без зависимостей от старого кода**

✅ Только наши модули: data.py, metrics.py, indicators.py, collector.py, main.py
✅ НОЛЬ импортов из старого бота (now_command, portfolio, orders и т.д.)
✅ Все данные сохраняются в /data (Render Disk)
✅ Атомарные операции (защита от коррупции)
✅ Таймфреймы: 12h, 6h, 4h, 2h
✅ Метрики: ticker + filters + klines + SMA14 + ATR14

## Переменные окружения

- `BOT_TOKEN` — токен Telegram бота
- `ADMIN_CHAT_ID` — чат админа
- `WEBHOOK_BASE` — базовый URL для webhook
- `PORT` — порт (по умолчанию 10000)
- `DATA_STORAGE` — путь хранилища (по умолчанию /data на Render Disk)

## Команды

- `/start` — запуск
- `/coins PAIR1 PAIR2` — добавить пары
- `/now` — **СОБРАТЬ ВСЕ МЕТРИКИ И СОХРАНИТЬ В /data**
- `/data` — список файлов в хранилище

## Структура хранилища (/data)

```
/data/
├── pairs.txt          # список пар
├── BTCUSDT.json       # полные метрики
├── ETHUSDT.json
└── BNBUSDT.json
```

## JSON структура (ETHUSDT.json)

```json
{
  "symbol": "ETHUSDT",
  "timestamp": "2025-11-12T13:00:00+00:00",
  "ticker": {
    "price": 3450.36,
    "bid_price": 3450.24,
    ...
  },
  "filters": {
    "price_filter": {"tick_size": "0.01", ...},
    "lot_size": {"min_qty": "0.001", ...},
    ...
  },
  "timeframes": {
    "12h": {
      "klines": [...100 свечей...],
      "indicators": {"sma14": 3441.12, "atr14": 140.20}
    },
    ...
  }
}
```

## Развертывание на Render

1. Загрузить v4.7
2. Выбрать Python 3
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Убедиться, что Render Disk смонтирован на /data
6. Задать env переменные
7. Запустить

## Гарантии

✓ Данные переживают рестарт (сохраняются в Render Disk /data)
✓ Защита от коррупции (атомарные операции)
✓ Полная совместимость с концепцией бота
✓ Готово к расширению (DCA-сетка, флаги, ордера)

---
Version 4.7 - 12.11.2025
