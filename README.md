# Trader Bot — версия 1.8

Телеграм-бот под деплой на Render, работающий через webhook.

## Основные возможности

- Вебхук для Telegram через FastAPI.
- Health-check эндпоинты: `GET /health` и `HEAD /health`.
- Уведомление админа при запуске: сообщение "Бот запущен. Версия 1.8".
- Команда `/start` отвечает: "Бот онлайн. Версия 1.8".

## Команды бота

См. файл `Bot_commands.txt`, команда `/help` отправляет его содержимое в чат.

Кратко:

- `/symbols` — управление списком торговых пар (хранится в `STORAGE_DIR/symbols_list.json`).
- `/now` — сбор и пересчёт сырых данных по символам:
  - тянет с Binance до 100 свечей по `TF1` и `TF2`,
  - записывает в `<SYMBOL>.json`:
    - свечи по каждому TF (до 100 штук),
    - массивы `ma30_arr`, `ma90_arr`, `atr14_arr`,
    - сигналы по каждому TF (`UP/DOWN/RANGE`),
    - агрегированный `market_mode`,
    - блок `trading_params`:
      - текущие цены (`last`, `bid`, `ask`),
      - `symbol_info` с `tick_size`, `step_size`, `min_qty`, `min_notional`, базовой и котируемой валютой,
      - сырые `filters` из `exchangeInfo`,
      - комиссии `fees` (пока статичные).
  - в файл `<SYMBOL>raw_market.jsonl` построчно складывает снэпшоты:
    - `ts`, `symbol`, `market_mode`, `tf1`, `tf2`, `signal_tf1`, `signal_tf2`.
- `/data` — работа с файлами в `STORAGE_DIR`:
  - список файлов, экспорт, удаление, импорт по caption `/data import`.

## Переменные окружения

- `BOT_TOKEN` — токен Telegram-бота
- `ADMIN_CHAT_ID` — ID админского канала/чата
- `ADMIN_KEY` — ключ админа (зарезервировано)
- `BINANCE_API_KEY` — ключ Binance (пока не используется)
- `BINANCE_API_SECRET` — секрет Binance (пока не используется)
- `HTTP_PROXY` — HTTP-прокси (пока не используется)
- `HTTPS_PROXY` — HTTPS-прокси (пока не используется)
- `RAW_MAX_BYTES` — лимит размера raw-файлов (пока не используется)
- `STORAGE_DIR` — корень диска на Render для хранения json/jsonl
- `WEBHOOK_BASE` — публичный URL сервиса на Render без завершающего `/`
- `TF1` — старший таймфрейм (например, "12")
- `TF2` — младший таймфрейм (например, "6")
- `MARKET_PUBLISH` — окно (в часах) для голосования /market и /market force (по умолчанию 24)

## История версий (кратко)

- **1.8** — команды `/market` и `/market force`, агрегированный `SYMBOLstate.json` по окну `MARKET_PUBLISH`.
- **1.7** — расширенный `/now`: 100 свечей, расчёт MA/ATR/сигналов, `market_mode`, расширенный `trading_params`, лог `raw_market.jsonl`.
- **1.6 и ниже** — базовый скелет бота, `/symbols`, `/help`, `/now` с созданием каркаса сырья, `/data` для работы с файлами.
