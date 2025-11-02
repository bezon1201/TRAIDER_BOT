# TRAIDER_BOT — v6

Добавлено:
- `/invested <sum>` (синоним `/invest <sum>`): меняет накопленную сумму инвестиций (можно отрицательно). Хранение в `/data/portfolio.json`.
- `/portfolio`: теперь выводит Total / Invested / Profit. Файл создаётся автоматически, если отсутствует.

Переменные:
- `STORAGE_DIR=/data` (по умолчанию `/data`).