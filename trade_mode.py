import os
import json
from pathlib import Path
from typing import Optional

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.exceptions import SkipHandler

# --- Файловое хранилище режима торговли ---

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)
TRADE_MODE_PATH = STORAGE_PATH / "trade_mode.json"

VALID_TRADE_MODES = {"sim", "live"}


def _format_mode_human(mode: str) -> str:
    if mode == "live":
        return "live (боевой режим)"
    # по умолчанию считаем sim
    return "sim (симуляция)"


def get_trade_mode() -> str:
    """
    Прочитать текущий режим торговли из trade_mode.json.

    Если файла нет, битый JSON или значение не из VALID_TRADE_MODES —
    вернуть безопасный режим по умолчанию: "sim".
    """
    try:
        if not TRADE_MODE_PATH.exists():
            return "sim"
        data = json.loads(TRADE_MODE_PATH.read_text(encoding="utf-8"))
        mode = data.get("mode")
        if mode in VALID_TRADE_MODES:
            return mode
    except Exception:
        # Любые ошибки чтения/парсинга — безопасный дефолт.
        pass
    return "sim"


def set_trade_mode(mode: str) -> None:
    """
    Установить режим торговли и записать его в trade_mode.json.
    """
    if mode not in VALID_TRADE_MODES:
        raise ValueError(f"Некорректный режим торговли: {mode!r}")

    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    payload = {"mode": mode}
    TRADE_MODE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# --- Telegram-логика команды /trade (исключение для этого модуля) ---

router = Router()

# Ожидаемый режим, для которого ждём PIN. None = ничего не ждём.
_pending_mode: Optional[str] = None

# PIN берём из переменной окружения один раз при импорте.
ADMIN_KEY = os.environ.get("ADMIN_KEY", "").strip()


@router.message(Command("trade"))
async def cmd_trade(message: types.Message) -> None:
    """Команда /trade и её варианты.

    /trade
    /trade mode
        Показать текущий режим торговли.

    /trade mode sim
    /trade mode live
        Запросить смену режима (нужен PIN, если ADMIN_KEY задан).
    """
    global _pending_mode

    text = (message.text or "").strip()
    # Разбираем аргументы вручную: /trade [mode] [sim|live]
    parts = text.split()
    # parts[0] == "/trade" или "/trade@бот"
    args = parts[1:]

    # Без аргументов — просто показать режим
    if not args or (len(args) == 1 and args[0].lower() == "mode"):
        current = get_trade_mode()
        await message.answer(f"Текущий режим торговли: {_format_mode_human(current)}")
        # ни на что не ждём PIN
        _pending_mode = None
        return

    # Ожидаем ровно два аргумента: "mode" и новый режим
    if len(args) != 2 or args[0].lower() != "mode":
        await message.answer(
            "Неверный формат команды. Примеры:\n"
            "/trade — показать текущий режим\n"
            "/trade mode sim  — переключить в симуляцию (нужен PIN)\n"
            "/trade mode live — переключить в боевой режим (нужен PIN)"
        )
        _pending_mode = None
        return

    requested_mode = args[1].lower()
    if requested_mode not in VALID_TRADE_MODES:
        await message.answer(
            "Некорректный режим. Используйте только: sim или live.\n"
            "Примеры:\n"
            "/trade mode sim\n"
            "/trade mode live"
        )
        _pending_mode = None
        return

    current = get_trade_mode()
    if requested_mode == current:
        await message.answer(
            f"Режим торговли уже установлен как {_format_mode_human(current)}."
        )
        _pending_mode = None
        return

    if not ADMIN_KEY:
        await message.answer(
            "Смена режима торговли недоступна: ADMIN_KEY не задан.\n"
            "Задайте переменную окружения ADMIN_KEY на сервере и перезапустите бота."
        )
        _pending_mode = None
        return

    # Запускаем процедуру подтверждения PIN
    _pending_mode = requested_mode
    await message.answer(
        f"Запрошено изменение режима торговли: "
        f"{_format_mode_human(current)} → {_format_mode_human(requested_mode)}.\n"
        f"Для подтверждения введите админ PIN."
    )


@router.message()
async def handle_trade_pin(message: types.Message) -> None:
    """Обработка ввода PIN для смены режима торговли.

    Этот хэндлер не должен мешать другим командам:
    - если _pending_mode is None → сразу SkipHandler
    - если сообщение команда (/...) или пустое → отменяем ожидание и SkipHandler
    """
    global _pending_mode

    # Если мы ничего не ждём — передаём апдейт дальше.
    if _pending_mode is None:
        raise SkipHandler()

    text = (message.text or "").strip()

    # Пользователь прислал команду вместо PIN — считаем, что он передумал.
    # Сбрасываем ожидание и даём обработать команду другим хэндлерам.
    if not text or text.startswith("/"):
        _pending_mode = None
        raise SkipHandler()

    # На всякий случай проверим ADMIN_KEY ещё раз
    if not ADMIN_KEY:
        pending = _pending_mode
        _pending_mode = None
        await message.answer(
            "Смена режима торговли недоступна: ADMIN_KEY не задан. "
            "Режим не изменён."
        )
        return

    # Сравниваем PIN
    if text != ADMIN_KEY:
        _pending_mode = None
        await message.answer("Неверный PIN, режим торговли не изменён.")
        return

    # PIN корректный — меняем режим
    old_mode = get_trade_mode()
    new_mode = _pending_mode or old_mode
    _pending_mode = None

    if new_mode not in VALID_TRADE_MODES:
        await message.answer("Ошибка: запрошен некорректный режим торговли.")
        return

    if new_mode == old_mode:
        await message.answer(
            f"Режим торговли уже установлен как {_format_mode_human(old_mode)}."
        )
        return

    set_trade_mode(new_mode)
    await message.answer(
        f"Режим торговли изменён: "
        f"{_format_mode_human(old_mode)} → {_format_mode_human(new_mode)}."
    )
