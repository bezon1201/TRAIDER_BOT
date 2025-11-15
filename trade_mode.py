
import os
import json
from pathlib import Path
from typing import Optional

from aiogram import Router, types
from aiogram.filters import Command

# --- Файловое хранилище режима торговли ---

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)
TRADE_MODE_PATH = STORAGE_PATH / "trade_mode.json"

VALID_TRADE_MODES = {"sim", "live"}

# --- ENV: админский PIN ---

ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

# --- Публичные функции работы с trade_mode.json ---


def get_trade_mode() -> str:
    """
    Вернуть текущий режим торговли: 'sim' или 'live'.

    Если файл отсутствует, битый или содержит неожиданные данные,
    возвращаем 'sim' без выброса исключений.
    """
    if not TRADE_MODE_PATH.exists():
        return "sim"

    try:
        raw = TRADE_MODE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        value = str(data.get("trade_mode", "")).lower()
        if value in VALID_TRADE_MODES:
            return value
    except Exception:
        # Любые ошибки чтения/десериализации приводят к дефолту.
        pass

    return "sim"


def set_trade_mode(mode: str) -> None:
    """
    Сохранить режим торговли в trade_mode.json.

    Принимает только 'sim' или 'live', иначе поднимает ValueError.
    """
    mode = str(mode).lower()
    if mode not in VALID_TRADE_MODES:
        raise ValueError(f"Invalid trade mode: {mode}")

    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    payload = {"trade_mode": mode}
    TRADE_MODE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


# --- Telegram-хэндлеры команды /trade (исключение для этого модуля) ---

router = Router()

# Ожидаемый новый режим во время запроса смены (если None — ничего не ждём).
_pending_mode: Optional[str] = None


def _format_mode_human(mode: str) -> str:
    if mode == "live":
        return "live (боевой режим)"
    return "sim (симуляция)"


@router.message(Command("trade"))
async def cmd_trade(message: types.Message) -> None:
    """
    /trade                — показать текущий режим.
    /trade mode           — то же самое.
    /trade mode sim|live  — запросить смену режима (через PIN).
    """
    global _pending_mode

    text = (message.text or "").strip()
    parts = text.split()

    # Нет аргументов или только "mode" -> просто выводим текущий режим.
    if len(parts) == 1 or (len(parts) == 2 and parts[1].lower() == "mode"):
        current = get_trade_mode()
        await message.answer(f"Текущий режим торговли: {_format_mode_human(current)}")
        return

    # Ожидаем синтаксис: /trade mode sim|live
    if len(parts) >= 3 and parts[1].lower() == "mode":
        requested = parts[2].lower()
        if requested not in VALID_TRADE_MODES:
            await message.answer(
                "Некорректный режим.\n"
                "Используйте: /trade mode sim или /trade mode live."
            )
            return

        current = get_trade_mode()
        if requested == current:
            await message.answer(
                f"Режим торговли уже установлен как {_format_mode_human(current)}."
            )
            _pending_mode = None
            return

        if not ADMIN_KEY:
            await message.answer(
                "Смена режима торговли недоступна: ADMIN_KEY не задан.\n"
                "Настройте переменную окружения ADMIN_KEY на сервере."
            )
            _pending_mode = None
            return

        # Запрашиваем PIN и запоминаем, какой режим хотим установить.
        _pending_mode = requested
        await message.answer(
            f"Запрошено изменение режима торговли: "
            f"{_format_mode_human(current)} → {_format_mode_human(requested)}.\n"
            "Для подтверждения введите админ PIN."
        )
        return

    # Любой другой синтаксис — маленькая подсказка по команде.
    await message.answer(
        "Использование команды /trade:\n"
        "/trade — показать текущий режим торговли.\n"
        "/trade mode — показать текущий режим торговли.\n"
        "/trade mode sim — запросить переключение в режим симуляции (нужен PIN).\n"
        "/trade mode live — запросить переключение в боевой режим (нужен PIN)."
    )


@router.message()
async def handle_trade_pin(message: types.Message) -> None:
    """
    Обработка PIN для смены режима торговли.

    Работает только если `_pending_mode` не None.
    Любая команда (начинающаяся с `/`) отменяет ожидание PIN.
    """
    global _pending_mode

    if _pending_mode is None:
        # Ничего не ждём — передаём сообщение дальше другим хэндлерам.
        return

    text = (message.text or "").strip()

    # Команда пользователем — отменяем запрос и не трогаем режим.
    if not text or text.startswith("/"):
        _pending_mode = None
        return

    # Здесь считаем, что пользователь ввёл PIN.
    if text == ADMIN_KEY:
        old_mode = get_trade_mode()
        new_mode = _pending_mode
        try:
            set_trade_mode(new_mode)
        finally:
            _pending_mode = None

        await message.answer(
            f"Режим торговли изменён: "
            f"{_format_mode_human(old_mode)} → {_format_mode_human(new_mode)}."
        )
    else:
        _pending_mode = None
        await message.answer("Неверный PIN, режим торговли не изменён.")
