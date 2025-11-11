# Telegram Bot - Python

Telegram-бот на Python с поддержкой webhook, прокси и размещением на Render.com

## Версии

### v1.0 (12.11.2025)
- ✅ Базовая структура бота на python-telegram-bot
- ✅ Webhook для работы на Render.com
- ✅ Поддержка прокси для статического IP (HTTP_PROXY, HTTPS_PROXY)
- ✅ Отправка сообщения "Бот запущен" в admin chat при старте
- ✅ Health check endpoint `/health` для UptimeRobot (HEAD/GET)
- ✅ Команда /start

## Требования

- Python 3.9+
- Переменные окружения (см. `.env.example`)

## Установка локально

1. Клонируй репозиторий:
```bash
git clone https://github.com/your-username/telegram-bot.git
cd telegram-bot
```

2. Установи зависимости:
```bash
pip install -r requirements.txt
```

3. Создай файл `.env` на основе `.env.example` и заполни переменные

4. Запусти бота:
```bash
python main.py
```

## Деплой на Render.com

1. Создай новый **Web Service** на [Render.com](https://render.com)
2. Подключи GitHub репозиторий
3. Настройки:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
4. Добавь Environment Variables (из скриншота):
   - `BOT_TOKEN`
   - `ADMIN_CHAT_ID`
   - `WEBHOOK_BASE` (URL твоего Render app, например: `https://your-app.onrender.com`)
   - `HTTP_PROXY` и `HTTPS_PROXY`
   - Все остальные переменные из `.env.example`

## UptimeRobot мониторинг

1. Создай новый монитор на [UptimeRobot.com](https://uptimerobot.com)
2. Тип: **HTTP(s)** или **HEAD**
3. URL: `https://your-app.onrender.com/health`
4. Интервал проверки: 5 минут

Это предотвратит "засыпание" бесплатного Render web service.

## Структура проекта

```
.
├── main.py              # Основной файл бота
├── requirements.txt     # Python зависимости
├── Procfile            # Команда запуска для Render
├── .env.example        # Пример переменных окружения
└── README.md           # Документация
```

## Endpoints

- `/health` - Health check для мониторинга (HEAD/GET)
- `/webhook` - Webhook endpoint для Telegram

## Команды бота

- `/start` - Приветственное сообщение

## Следующие шаги

- Добавление команд для работы с Binance API
- Реализация обработчиков сообщений
- Хранилище данных
- Расширенная логика бота

## Лицензия

MIT
