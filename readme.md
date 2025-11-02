# TRAIDER Bot — каркас

Тип: Web Service (Render). Только HEAD health и Telegram webhook.

## ENV
- `TRAIDER_BOT_TOKEN`
- `TRAIDER_ADMIN_CAHT_ID`  ← как задано
- `TRAIDER_ACTIVE_CHAT_ID`
- `TRAIDER_WEBHOOK_BASE` (позже)
- `TRAIDER_METRIC_CHAT_ID` (позже)

## Маршруты
- `HEAD /health` — для UptimeRobot (только HEAD).
- `POST /telegram` — webhook.

## Запуск
```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

## Changelog
- **1** — каркас: маршруты `HEAD /health`, `POST /telegram`; при старте отправляется сообщение
  `<UTC> Бот запущен` в чаты `TRAIDER_ADMIN_CAHT_ID` и `TRAIDER_ACTIVE_CHAT_ID`.
