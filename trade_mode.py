import json
import os
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
    try:
        if TRADE_MODE_PATH.exists():
            with TRADE_MODE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            mode = data.get("mode")
            if mode in VALID_TRADE_MODES:
                return mode
    except Exception:
        # Любые ошибки чтения/парсинга не должны ломать бота
        pass
    return DEFAULT_MODE


def set_trade_mode(mode: str) -> None:
    """Установить режим торговли и записать его в trade_mode.json."""
    if mode not in VALID_TRADE_MODES:
        raise ValueError(f"Invalid trade mode: {mode}")

    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    with TRADE_MODE_PATH.open("w", encoding="utf-8") as f:
        json.dump({"mode": mode}, f, ensure_ascii=False)


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
        STORAGE_PATH.mkdir(parents=True, exist_ok=True)
        with CONTROL_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        # Логирование не должно ломать основную логику бота
        pass


def _mode_label(mode: str) -> str:
    return "симуляция" if mode == "sim" else "боевой режим"


def _format_current_mode(mode: str) -> str:
    return f"Текущий режим торговли: {mode} ({_mode_label(mode)})."


def _format_change_request(old: str, new: str) -> str:
    return (
        "Запрошено изменение режима торговли: "
        f"{old} ({_mode_label(old)}) → {new} ({_mode_label(new)})."
    )


def _format_changed(old: str, new: str) -> str:
    if old == new:
        return f"Режим торговли остался: {new} ({_mode_label(new)})."
    return (
        "Режим торговли изменён: "
        f"{old} ({_mode_label(old)}) → {new} ({_mode_label(new)})."
    )


@router.message(Command("trade"))
async def cmd_trade(message: types.Message) -> None:
    """Команда /trade: просмотр и смена режима торговли.

    /trade
    /trade mode
        Показать текущий режим.

    /trade mode sim
    /trade mode live
        Запросить смену режима (требуется PIN = ADMIN_KEY).
    """
    global _pending_mode

    text = (message.text or "").strip()
    parts = text.split()

    current_mode = get_trade_mode()

    # /trade или /trade mode -> просто показать текущий режим
    if len(parts) == 1 or (len(parts) == 2 and parts[1].lower() == "mode"):
        _pending_mode = None
        await message.answer(_format_current_mode(current_mode))
        return

    # /trade mode <sim|live>
    if len(parts) == 3 and parts[1].lower() == "mode":
        requested = parts[2].lower()

        if requested not in VALID_TRADE_MODES:
            _pending_mode = None
            await message.answer(
                "Некорректный режим. Используйте: "
                "/trade mode sim или /trade mode live."
            )
            return

        if requested == current_mode:
            _pending_mode = None
            await message.answer(
                f"Режим торговли уже установлен как {requested}."
            )
            return

        if not ADMIN_KEY:
            _pending_mode = None
            await message.answer(
                "Смена режима торговли недоступна: ADMIN_KEY не задан. "
                "Режим торговли не изменён."
            )
            return

        _pending_mode = requested
        await message.answer(
            _format_change_request(current_mode, requested)
            + "\nДля подтверждения введите админ PIN."
        )
        return

    # Fallback: мини-help по /trade
    _pending_mode = None
    await message.answer(
        "Использование команды:\n"
        "/trade — показать текущий режим.\n"
        "/trade mode — показать текущий режим.\n"
        "/trade mode sim  — запросить переключение в режим симуляции (нужен PIN).\n"
        "/trade mode live — запросить переключение в боевой режим (нужен PIN)."
    )


@router.message()
@router.message()
@router.message()
async def handle_trade_pin(message: types.Message) -> None:
    """Обработка PIN для смены режима торговли.

    Работает только если ранее был запрос /trade mode sim|live
    и в _pending_mode сохранён целевой режим.
    """
    # Если смена режима не запрошена — не перехватываем апдейт
    if _pending_mode is None:
        raise SkipHandler()

    text = (message.text or "").strip()

    # Если пришла команда (/...) или пустой текст — сбрасываем ожидание PIN
    # и передаём апдейт дальше другим хэндлерам
    if not text or text.startswith("/"):
        _pending_mode = None
        raise SkipHandler()

    # ADMIN_KEY не задан — смена режима невозможна
    if not ADMIN_KEY:
        requested = _pending_mode
        _pending_mode = None
        await message.answer(
            "Смена режима торговли недоступна: ADMIN_KEY не задан. "
            "Режим не изменён."
        )
        return

    # Неверный PIN
    if text != ADMIN_KEY:
        _pending_mode = None
        await message.answer("Неверный PIN, режим торговли не изменён.")
        return

    # PIN верный — меняем режим
    old_mode = get_trade_mode()
    new_mode = _pending_mode or old_mode
    _pending_mode = None

    if new_mode not in VALID_TRADE_MODES:
        await message.answer("Ошибка: запрошен некорректный режим торговли.")
        return

    set_trade_mode(new_mode)
    _log_trade_mode_change(old_mode, new_mode)
    await message.answer(_format_changed(old_mode, new_mode))