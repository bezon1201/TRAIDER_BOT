import os
import json
from pathlib import Path
from typing import Optional

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.dispatcher.event.bases import SkipHandler

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)
TRADE_MODE_PATH = STORAGE_PATH / "trade_mode.json"

VALID_TRADE_MODES = {"sim", "live"}

ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

router = Router()

# Один пользователь — достаточно одного модульного состояния
_pending_mode: Optional[str] = None


def get_trade_mode() -> str:
    """Вернуть текущий режим торговли: 'sim' или 'live'.

    Если файл отсутствует, повреждён или значение вне допустимых,
    возвращаем 'sim' без поднятия исключения.
    """
    try:
        data = json.loads(TRADE_MODE_PATH.read_text(encoding="utf-8"))
        mode = str(data.get("trade_mode", "")).lower()
        if mode in VALID_TRADE_MODES:
            return mode
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        pass
    return "sim"


def set_trade_mode(mode: str) -> None:
    """Сохранить режим торговли в trade_mode.json.

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


def _describe_mode(mode: str) -> str:
    mode = (mode or "").lower()
    if mode == "live":
        return "live (боевой режим)"
    # по умолчанию считаем sim
    return "sim (симуляция)"


@router.message(Command("trade"))
async def cmd_trade(message: types.Message) -> None:
    """Команда /trade и подкоманда /trade mode.

    /trade
    /trade mode
        — показать текущий режим.

    /trade mode sim
    /trade mode live
        — запросить смену режима (с PIN).
    """
    global _pending_mode

    text = (message.text or "").strip()
    parts = text.split()
    # parts[0] — это "/trade" или "/trade@botname"
    args = parts[1:]

    # /trade  или  /trade mode  без аргумента режима
    if not args or args[0].lower() == "mode" and len(args) == 1:
        current = get_trade_mode()
        _pending_mode = None
        await message.answer(f"Текущий режим торговли: {_describe_mode(current)}")
        return

    # /trade <что-то-другое>
    if args[0].lower() != "mode":
        _pending_mode = None
        await message.answer(
            "Использование:\n"
            "/trade — показать текущий режим торговли.\n"
            "/trade mode — показать текущий режим торговли.\n"
            "/trade mode sim  — запросить переключение в режим симуляции.\n"
            "/trade mode live — запросить переключение в боевой режим."
        )
        return

    # сюда попадаем только если есть как минимум 2 аргумента: 'mode <smth>'
    if len(args) < 2:
        current = get_trade_mode()
        _pending_mode = None
        await message.answer(f"Текущий режим торговли: {_describe_mode(current)}")
        return

    requested = args[1].lower()
    if requested not in VALID_TRADE_MODES:
        _pending_mode = None
        await message.answer("Некорректный режим. Используйте: sim или live.")
        return

    current = get_trade_mode()
    if requested == current:
        _pending_mode = None
        await message.answer(f"Режим торговли уже установлен как {_describe_mode(current)}.")
        return

    if not ADMIN_KEY:
        _pending_mode = None
        await message.answer(
            "Смена режима торговли недоступна: ADMIN_KEY не задан.\n"
            "Режим не изменён."
        )
        return

    _pending_mode = requested
    await message.answer(
        f"Запрошено изменение режима торговли: {_describe_mode(current)} → {_describe_mode(requested)}.\n"
        "Для подтверждения введите админ PIN."
    )


@router.message()
async def handle_trade_pin(message: types.Message) -> None:
    """Обработка PIN после /trade mode sim|live.

    Работает только когда *_pending_mode* не None.
    """
    global _pending_mode

    # Если смена режима не запрошена — не трогаем этот апдейт
    if _pending_mode is None:
        raise SkipHandler()

    text = (message.text or "").strip()

    # Команда (/...) или пустое сообщение — считаем отменой ввода PIN;
    # сбрасываем ожидание и передаём апдейт другим хэндлерам.
    if not text or text.startswith("/"):
        _pending_mode = None
        raise SkipHandler()

    if not ADMIN_KEY:
        _pending_mode = None
        await message.answer(
            "Смена режима торговли недоступна: ADMIN_KEY не задан.\n"
            "Режим не изменён."
        )
        return

    # Неверный PIN
    if text != ADMIN_KEY:
        _pending_mode = None
        await message.answer("Неверный PIN, режим торговли не изменён.")
        return

    # Всё ок — меняем режим
    old_mode = get_trade_mode()
    new_mode = _pending_mode or old_mode
    _pending_mode = None

    set_trade_mode(new_mode)
    await message.answer(
        f"Режим торговли изменён: {_describe_mode(old_mode)} → {_describe_mode(new_mode)}."
    )
