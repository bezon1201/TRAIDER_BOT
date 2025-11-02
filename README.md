
# TRAIDER_BOT

Каркас бота для торговли на Binance (версия 4). Развёртывание — на Render. Выход в интернет — через статический IP (Google Cloud tinyproxy).

## Переменные окружения
- `TRAIDER_ADMIN_CAHT_ID` — канал/чат для системных сообщений
- `TRAIDER_BOT_TOKEN` — токен бота
- `TRAIDER_METRIC_CHAT_ID` — канал для метрик (пока не используется в v4)
- `TRAIDER_WEBHOOK_BASE` или `WEBHOOK_BASE` — базовый адрес вебхука (если нужно)
- `HTTP_PROXY`, `HTTPS_PROXY` — URL прокси вида `http://user:pass@IP:8888`

## Эндпоинты
- `GET /health` — 200 OK; подходит для HEAD-пингов UptimeRobot
- `POST /telegram` — заглушка вебхука (логику не меняем без запроса)

## Changelog
- **4** — Добавлена проверка соединения с Binance при старте (`/api/v3/ping`). В админ-чат уходит сообщение:
  ```
  YYYY-MM-DD HH:MM UTC Бот запущен
  Binance connection: ✅/❌
  ```
- 3 — (предыдущая версия, без изменений в этом архиве)
- 2 — (предыдущая версия)
- 1 — (предыдущая версия)
