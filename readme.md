# TRAIDER Bot — каркас

Тип: Web Service (Render). Только HEAD health и Telegram webhook.

## ENV
- `TRAIDER_BOT_TOKEN`
- `TRAIDER_ADMIN_CAHT_ID`  ← как задано
- `WEBHOOK_BASE` или `TRAIDER_WEBHOOK_BASE` — базовый URL (https://...)
- `TRAIDER_METRIC_CHAT_ID` (позже)

## Маршруты
- `HEAD /health` — для UptimeRobot (только HEAD).
- `POST /telegram` — webhook.

## Стартовое поведение
- Устанавливает Telegram webhook на `<BASE>/telegram`, если задан `WEBHOOK_BASE`/`TRAIDER_WEBHOOK_BASE`.
- Отправляет только в `TRAIDER_ADMIN_CAHT_ID` сообщение: `<UTC> Бот запущен`.

## Запуск
```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

## Changelog
- **1** — каркас: HEAD `/health`, POST `/telegram`; стартовые сообщения в два чата.
- **2** — авто-установка webhook; активный чат стал опциональным.
- **3** — по просьбе: убран «активный чат». Стартовое сообщение уходит **только** в `TRAIDER_ADMIN_CAHT_ID`.
