import os
import json
from pathlib import Path

from aiogram import Router, types
from aiogram.filters import Command

# --- Файл режима торговли ---

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)
TRADE_MODE_PATH = STORAGE_PATH / "trade_mode.json"

VALID_TRADE_MODES = {"sim", "live"}

# Админский PIN из переменной окружения
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

# Временное состояние для ожидаемой смены режима (один пользователь/бот)
_PENDING_TRADE_MODE: str | None = None

router = Router()


def get_trade_mode() -> str:
    """Вернуть текущий режим торговли: 'sim' или 'live'.

    Если файл отсутствует, битый или содержит неожиданное значение,
    тихо возвращает 'sim'.
    """
    if not TRADE_MODE_PATH.exists():
        return "sim"

    try:
        raw = TRADE_MODE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return "sim"

    mode = str(data.get("trade_mode", "sim")).lower()
    if mode not in VALID_TRADE_MODES:
        return "sim"
    return mode


def set_trade_mode(mode: str) -> None:
    """Сохранить режим торговли в trade_mode.json.

    Принимает только 'sim' или 'live'. При других значениях
    поднимает ValueError.
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


def _human_mode(mode: str) -> str:
    """Короткое текстовое описание режима для сообщений пользователю."""
    if mode == "live":
        return "боевой режим"
    return "симуляция"


@router.message(Command("trade"))
async def cmd_trade(message: types.Message) -> None:
    """Команда /trade: показ и смена режима торговли.

    Форматы:
    /trade
        — показать текущий режим.
    /trade mode
        — показать текущий режим с пояснением.
    /trade mode sim|live
        — запросить смену режима (нужен PIN).
    """
    global _PENDING_TRADE_MODE

    text = (message.text or "").strip()
    parts = text.split()

    # /trade — просто показать режим
    if len(parts) == 1:
        current = get_trade_mode()
        await message.answer(
            f"Текущий режим торговли: {current} ({_human_mode(current)})"
        )
        _PENDING_TRADE_MODE = None
        return

    # дальше интересует только подкоманда mode
    if len(parts) >= 2 and parts[1].lower() == "mode":
        # /trade mode — тоже просто показать режим
        if len(parts) == 2:
            current = get_trade_mode()
            await message.answer(
                f"Текущий режим торговли: {current} ({_human_mode(current)})"
            )
            _PENDING_TRADE_MODE = None
            return

        # /trade mode <mode>
        requested = parts[2].lower()
        if requested not in VALID_TRADE_MODES:
            await message.answer(
                "Некорректный режим. Используйте: /trade mode sim или /trade mode live"
            )
            _PENDING_TRADE_MODE = None
            return

        current = get_trade_mode()
        if requested == current:
            await message.answer(
                f"Режим торговли уже установлен как {current} ({_human_mode(current)})"
            )
            _PENDING_TRADE_MODE = None
            return

        if not ADMIN_KEY:
            await message.answer(
                "Смена режима торговли недоступна: ADMIN_KEY не задан."
            )
            _PENDING_TRADE_MODE = None
            return

        _PENDING_TRADE_MODE = requested
        await message.answer(
            "Запрошено изменение режима торговли:
"
            f"{current} ({_human_mode(current)}) → "
            f"{requested} ({_human_mode(requested)})

"
            "Для подтверждения введите админ PIN."
        )
        return

    # неизвестная подкоманда
    await message.answer(
        "Неизвестная команда /trade.
"
        "Используйте:
"
        "/trade — показать текущий режим
"
        "/trade mode sim|live — сменить режим"
    )
    _PENDING_TRADE_MODE = None


@router.message()
async def trade_mode_pin_handler(message: types.Message) -> None:
    """Обработка PIN для смены режима торговли.

    Работает только если ранее была запрошена смена режима через /trade mode.
    Любая команда (сообщение, начинающееся с '/') сбрасывает ожидание PIN.
    """
    global _PENDING_TRADE_MODE

    # Если смена режима не запрошена — просто выходим
    if _PENDING_TRADE_MODE is None:
        return

    text = (message.text or "").strip() if message.text is not None else ""

    # Любая новая команда отменяет ожидание PIN
    if text.startswith("/"):
        _PENDING_TRADE_MODE = None
        return

    # Пустые/нетекстовые сообщения игнорируем, оставляя ожидание PIN
    if not text:
        return

    if not ADMIN_KEY:
        await message.answer(
            "Смена режима торговли недоступна: ADMIN_KEY не задан."
        )
        _PENDING_TRADE_MODE = None
        return

    # Проверка PIN
    if text == ADMIN_KEY:
        old_mode = get_trade_mode()
        requested = _PENDING_TRADE_MODE
        try:
            set_trade_mode(requested)
        except Exception:
            _PENDING_TRADE_MODE = None
            await message.answer(
                "Не удалось изменить режим торговли из-за внутренней ошибки."
            )
            return

        _PENDING_TRADE_MODE = None
        await message.answer(
            "Режим торговли изменён:
"
            f"{old_mode} ({_human_mode(old_mode)}) → "
            f"{requested} ({_human_mode(requested)})"
        )
    else:
        _PENDING_TRADE_MODE = None
        await message.answer("Неверный PIN, режим торговли не изменён.")
