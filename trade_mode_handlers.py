import os
import logging
from typing import Dict

from aiogram import Router, types

from trade_mode import get_trade_mode, set_trade_mode

router = Router()

ADMIN_KEY = os.environ.get("ADMIN_KEY", "").strip()

# Для личного бота достаточно отслеживать ожидание PIN по chat_id.
_pending_mode_by_chat: Dict[int, str] = {}

logger = logging.getLogger(__name__)


@router.message()
async def handle_trade_and_pin(message: types.Message) -> None:
    """Общая обработка /trade mode и ввода PIN.

    Логика:
    - если для чата ждём PIN:
        - любая команда (текст начинается с '/') отменяет запрос и очищает ожидание;
        - иначе считаем сообщение PIN и проверяем его.
    - если PIN не ждём (или только что отменили) — обрабатываем команды /trade.
    """
    if not message.text:
        return

    text = message.text.strip()
    chat_id = message.chat.id
    logger.info("[trade_mode] incoming message chat_id=%s text=%r", chat_id, text)

    # Если уже ждём PIN для этого чата — обрабатываем этот сценарий.
    if chat_id in _pending_mode_by_chat:
        requested = _pending_mode_by_chat[chat_id]
        logger.info("[trade_mode] pending PIN chat_id=%s requested=%s", chat_id, requested)

        # Любая команда во время ожидания PIN отменяет переход.
        if text.startswith("/"):
            _pending_mode_by_chat.pop(chat_id, None)
            logger.info("[trade_mode] command %r while waiting PIN, cancel request", text)
            await message.answer("Запрос смены режима торговли отменён.")

            # Если это снова /trade, позволяем обработать её как новую команду.
            if not text.startswith("/trade"):
                return

            # Переписываем parts/text и продолжаем как обычную /trade-команду.
            logger.info("[trade_mode] re-processing %r as new /trade command", text)
        else:
            # Считаем сообщение попыткой ввода PIN.
            _pending_mode_by_chat.pop(chat_id, None)

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

            descr_new = "live (боевой режим)" if requested == "live" else "sim (симуляция)"
            descr_old = "live (боевой режим)" if old_mode == "live" else "sim (симуляция)"

            await message.answer(
                f"Режим торговли изменён: **{descr_old} → {descr_new}**.\n\n"
                f"Текущий режим: **{descr_new}**."
            )
            return

    # Если PIN не ждём (или только что отменили и это не /trade) — обрабатываем только /trade-команды.
    if not text.startswith("/trade"):
        return

    parts = text.split()
    logger.info("[trade_mode] handle /trade command parts=%s", parts)

    # /trade — краткий хелп по блоку
    if len(parts) == 1:
        await message.answer(
            "Использование:\n"
            "/trade mode — показать текущий режим торговли.\n"
            "/trade mode sim — запросить переключение в режим симуляции (через PIN).\n"
            "/trade mode live — запросить переключение в боевой режим (через PIN)."
        )
        return

    # /trade mode ...
    if parts[1].lower() != "mode":
        await message.answer(
            "Неизвестная команда /trade.\n"
            "Использование:\n"
            "/trade mode — показать текущий режим торговли.\n"
            "/trade mode sim — запросить переключение в режим симуляции (через PIN).\n"
            "/trade mode live — запросить переключение в боевой режим (через PIN)."
        )
        return

    # /trade mode — показать текущий режим
    if len(parts) == 2:
        current = get_trade_mode()
        logger.info("[trade_mode] /trade mode current=%s", current)

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

    _pending_mode_by_chat[chat_id] = requested
    requested_text = "live (боевой режим)" if requested == "live" else "sim (режим симуляции)"

    logger.info(
        "[trade_mode] requested change chat_id=%s from %s to %s",
        chat_id,
        current,
        requested,
    )

    await message.answer(
        f"Текущий режим торговли: **{current}**.\n\n"
        f"Вы запросили переключение в режим **{requested_text}**.\n\n"
        "Для подтверждения введите админский PIN одним сообщением.\n"
        "Без правильного PIN режим торговли не изменится."
    )
