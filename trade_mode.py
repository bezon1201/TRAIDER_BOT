import os
import json
import time
from pathlib import Path
from typing import Optional

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.dispatcher.event.bases import SkipHandler

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)
STORAGE_PATH.mkdir(parents=True, exist_ok=True)

TRADE_MODE_PATH = STORAGE_PATH / "trade_mode.json"
CONTROL_LOG_PATH = STORAGE_PATH / "control_log.jsonl"

VALID_TRADE_MODES = {"sim", "live"}
DEFAULT_MODE = "sim"

ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

router = Router()

_pending_mode: Optional[str] = None


def get_trade_mode() -> str:
    """Прочитать текущий режим торговли из trade_mode.json.

    Если файл отсутствует или повреждён — вернуть режим по умолчанию (sim).
    """
    if not TRADE_MODE_PATH.exists():
        return DEFAULT_MODE

    try:
        with TRADE_MODE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        mode = data.get("mode")
        if mode in VALID_TRADE_MODES:
            return mode
    except Exception:
        # В случае любой ошибки не падаем, а возвращаем режим по умолчанию
        pass

    return DEFAULT_MODE


def set_trade_mode(mode: str) -> None:
    """Сохранить режим торговли в trade_mode.json."""
    if mode not in VALID_TRADE_MODES:
        raise ValueError(f"Invalid trade mode: {mode!r}")

    TRADE_MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRADE_MODE_PATH.open("w", encoding="utf-8") as f:
        json.dump({"mode": mode}, f, ensure_ascii=False)


def _format_mode(mode: str) -> str:
    if mode == "sim":
        return "sim (симуляция)"
    if mode == "live":
        return "live (боевой режим)"
    return mode


def _format_current_mode_message(mode: str) -> str:
    return f"Текущий режим торговли: {_format_mode(mode)}"


def _format_changed(old: str, new: str) -> str:
    return f"Режим торговли изменён: {_format_mode(old)} → {_format_mode(new)}."


def _log_trade_mode_change(old: str, new: str) -> None:
    """Записать событие смены режима в control_log.jsonl.

    Формат строки:
    {
      "event": "trade_mode_changed",
      "from": "sim",
      "to": "live",
      "ts": 1234567890
    }
    """
    if old == new:
        return

    event = {
        "event": "trade_mode_changed",
        "from": old,
        "to": new,
        "ts": int(time.time()),
    }

    try:
        CONTROL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONTROL_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        # Логирование не должно ломать основную логику бота
        pass


@router.message(Command("trade"))
async def cmd_trade(message: types.Message) -> None:
    """Команда /trade и подкоманда /trade mode.

    /trade, /trade mode              — показать текущий режим
    /trade mode sim  (нужен PIN)     — переключить в режим симуляции
    /trade mode live (нужен PIN)     — переключить в боевой режим
    """
    global _pending_mode

    text = (message.text or "").strip()
    parts = text.split()

    # /trade или /trade@bot
    if len(parts) == 1:
        current = get_trade_mode()
        _pending_mode = None
        await message.answer(_format_current_mode_message(current))
        return

    subcommand = parts[1].lower()

    # /trade mode ...
    if subcommand != "mode":
        _pending_mode = None
        await message.answer(
            "Использование:\n"
            "/trade — показать текущий режим торговли\n"
            "/trade mode — показать текущий режим торговли\n"
            "/trade mode sim  — переключить в режим симуляции (нужен PIN)\n"
            "/trade mode live — переключить в боевой режим (нужен PIN)"
        )
        return

    # /trade mode
    if len(parts) == 2:
        current = get_trade_mode()
        _pending_mode = None
        await message.answer(_format_current_mode_message(current))
        return

    # /trade mode sim|live
    requested = parts[2].lower()
    if requested not in VALID_TRADE_MODES:
        _pending_mode = None
        await message.answer(
            "Некорректный режим. Используйте: sim или live.\n"
            "Пример: /trade mode sim"
        )
        return

    current = get_trade_mode()
    if requested == current:
        _pending_mode = None
        await message.answer(
            f"Режим торговли уже установлен как {_format_mode(current)}."
        )
        return

    if not ADMIN_KEY:
        _pending_mode = None
        await message.answer(
            "Смена режима торговли недоступна: ADMIN_KEY не задан. "
            "Режим не изменён."
        )
        return

    _pending_mode = requested
    await message.answer(
        "Запрошено изменение режима торговли: "
        f"{_format_mode(current)} → {_format_mode(requested)}.\n"
        "Для подтверждения введите админ PIN."
    )


@router.message()
async def handle_trade_pin(message: types.Message) -> None:
    """Обработка PIN для смены режима торговли.

    Работает только если ранее был запрос /trade mode sim|live
    и в _pending_mode сохранён целевой режим.
    """
    global _pending_mode

    # Если смена режима не запрошена — не перехватываем апдейт
    if _pending_mode is None:
        raise SkipHandler()

    text = (message.text or "").strip()

    # Если пришла команда (/...) или пустое сообщение — считаем,
    # что пользователь передумал, и пропускаем апдейт дальше
    if not text or text.startswith("/"):
        _pending_mode = None
        raise SkipHandler()

    # Если ADMIN_KEY не задан — на всякий случай сообщаем и сбрасываем состояние
    if not ADMIN_KEY:
        _pending_mode = None
        await message.answer(
            "Смена режима торговли недоступна: ADMIN_KEY не задан. "
            "Режим не изменён."
        )
        return

    # Проверяем PIN
    if text != ADMIN_KEY:
        _pending_mode = None
        await message.answer("Неверный PIN, режим торговли не изменён.")
        return

    # PIN корректный — меняем режим и логируем
    old_mode = get_trade_mode()
    new_mode = _pending_mode or old_mode
    _pending_mode = None

    if new_mode not in VALID_TRADE_MODES:
        await message.answer(
            "Внутренняя ошибка: некорректный целевой режим. "
            "Режим не изменён."
        )
        return

    set_trade_mode(new_mode)
    _log_trade_mode_change(old_mode, new_mode)
    await message.answer(_format_changed(old_mode, new_mode))
