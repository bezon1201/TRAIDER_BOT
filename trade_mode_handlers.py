import os
import logging
from typing import Dict

from aiogram import Router, types, F
from aiogram.filters import Command

from trade_mode import get_trade_mode, set_trade_mode

router = Router()

ADMIN_KEY = os.environ.get("ADMIN_KEY", "").strip()

# Для личного бота достаточно хранить ожидание PIN по chat_id.
_pending_mode_by_chat: Dict[int, str] = {}

logger = logging.getLogger("main")


@router.message(F.text.startswith("/trade"))
async def cmd_trade_mode(message: types.Message) -> None:
    """Обработчик команд /trade mode, /trade mode sim, /trade mode live."""
    text = (message.text or "").strip()
    parts = text.split()

    logger.info("[trade_mode] /trade command parts=%s", parts)

    # Ожидаем минимум: /trade mode
    if len(parts) < 2 or parts[1].lower() != "mode":
        await message.answer(
            "Неизвестная команда /trade.\n"
            "Использование:\n"
            "/trade mode — показать текущий режим торговли.\n"
            "/trade mode sim — запросить режим симуляции (через PIN).\n"
            "/trade mode live — запросить боевой режим (через PIN)."
        )
        return

    # /trade mode — показать текущий режим
    if len(parts) == 2:
        current = get_trade_mode()
        logger.info("[trade_mode] /trade mode show current=%s", current)
        if current == "sim":
            await message.answer(
                "Текущий режим торговли: **sim** (симуляция).\n\n"
                "Для смены режима используйте:\n"
                "/trade mode live — боевой режим (через PIN)."
            )
        else:
            await message.answer(
                "Текущий режим торговли: **live** (боевой режим).\n\n"
                "Для смены режима используйте:\n"
                "/trade mode sim — режим симуляции (через PIN)."
            )
        return

    # /trade mode <something>
    requested = parts[2].lower()

    if requested not in {"sim", "live"}:
        await message.answer(
            "Некорректный режим. Используйте:\n"
            "/trade mode sim\n"
            "/trade mode live"
        )
        return

    current = get_trade_mode()
    logger.info("[trade_mode] /trade mode %s requested, current=%s", requested, current)

    if requested == current:
        if current == "sim":
            await message.answer(
                "Режим торговли уже установлен как **sim** (симуляция).\n"
                "Ничего менять не нужно."
            )
        else:
            await message.answer(
                "Режим торговли уже установлен как **live** (боевой режим).\n"
                "Ничего менять не нужно."
            )
        return

    if not ADMIN_KEY:
        logger.warning("[trade_mode] ADMIN_KEY is not set, cannot change mode")
        await message.answer(
            "Смена режима торговли недоступна: переменная окружения ADMIN_KEY не задана."
        )
        return

    chat_id = message.chat.id
    _pending_mode_by_chat[chat_id] = requested

    if requested == "live":
        requested_text = "live (боевой режим)"
    else:
        requested_text = "sim (режим симуляции)"

    logger.info("[trade_mode] waiting PIN chat_id=%s requested=%s", chat_id, requested)

    await message.answer(
        f"Текущий режим торговли: **{current}**.\n\n"
        f"Вы запросили переключение в режим **{requested_text}**.\n\n"
        "Для подтверждения введите админский PIN одним сообщением.\n"
        "Без правильного PIN режим торговли не изменится."
    )


@router.message(F.text & ~F.text.startswith("/"))
async def handle_trade_pin(message: types.Message) -> None:
    """Обработка ввода PIN.

    Срабатывает только на текстовые сообщения, которые не начинаются с '/'.
    Другие команды (например, /data, /now) этим хендлером не перехватываются
    и могут свободно обрабатываться другими роутерами.
    """
    if not message.text:
        return

    chat_id = message.chat.id
    text = message.text.strip()

    if chat_id not in _pending_mode_by_chat:
        return

    requested = _pending_mode_by_chat.pop(chat_id, None)
    if requested is None:
        return

    logger.info(
        "[trade_mode] PIN attempt chat_id=%s requested=%s text=%r",
        chat_id,
        requested,
        text,
    )

    if text != ADMIN_KEY:
        logger.info("[trade_mode] wrong PIN for chat_id=%s", chat_id)
        await message.answer("Неверный PIN, режим торговли не изменён.")
        return

    old_mode = get_trade_mode()
    logger.info("[trade_mode] correct PIN, changing mode from %s to %s", old_mode, requested)

    try:
        set_trade_mode(requested)
    except Exception:
        logger.exception("[trade_mode] error while setting trade mode to %s", requested)
        await message.answer("Ошибка при смене режима торговли. Попробуйте ещё раз.")
        return

    if requested == "live":
        descr_new = "live (боевой режим)"
    else:
        descr_new = "sim (симуляция)"

    if old_mode == "live":
        descr_old = "live (боевой режим)"
    else:
        descr_old = "sim (симуляция)"

    await message.answer(
        f"Режим торговли изменён: **{descr_old} → {descr_new}**.\n\n"
        f"Текущий режим: **{descr_new}**."
    )
