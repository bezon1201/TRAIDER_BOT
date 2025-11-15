import os
import logging
from typing import Dict

from aiogram import Router, types

from trade_mode import get_trade_mode, set_trade_mode

router = Router()

ADMIN_KEY = os.environ.get("ADMIN_KEY", "").strip()
_pending_mode_by_chat: Dict[int, str] = {}

# Используем тот же логгер, что и main, чтобы записи точно попали в Render-логи.
logger = logging.getLogger("main")
logger.info("[trade_mode] trade_mode_handlers imported")


@router.message()
async def handle_trade_and_pin(message: types.Message) -> None:
    """Единый обработчик /trade mode и ввода PIN."""
    if not message.text:
        return

    text = message.text.strip()
    chat_id = message.chat.id

    logger.info("[trade_mode] incoming message chat_id=%s text=%r", chat_id, text)

    # 1. Если ждём PIN для этого чата — сначала обрабатываем его.
    if chat_id in _pending_mode_by_chat:
        requested = _pending_mode_by_chat[chat_id]
        logger.info("[trade_mode] pending PIN chat_id=%s requested=%s", chat_id, requested)

        if text.startswith("/"):
            # Любая команда отменяет переход.
            _pending_mode_by_chat.pop(chat_id, None)
            logger.info("[trade_mode] command %r while waiting PIN, cancel request", text)
            await message.answer("Запрос смены режима торговли отменён.")

            # Если команда не /trade — выходим, дадим другим хендлерам обработать её.
            if not text.startswith("/trade"):
                return

            # Если это снова /trade — продолжаем ниже как с новой командой.
        else:
            # Это попытка ввода PIN.
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

            descr_old = "sim (симуляция)" if old_mode == "sim" else "live (боевой режим)"
            descr_new = "sim (симуляция)" if requested == "sim" else "live (боевой режим)"

            await message.answer(
                f"Режим торговли изменён: **{descr_old} → {descr_new}**.\n\n"
                f"Текущий режим: **{descr_new}**."
            )
            return

    # 2. Больше не ждём PIN (или только что отменили). Обрабатываем /trade...
    if not text.startswith("/trade"):
        return

    parts = text.split()
    logger.info("[trade_mode] /trade command parts=%s", parts)

    # /trade или что-то непонятное
    if len(parts) == 1 or parts[1].lower() != "mode":
        await message.answer(
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

    # /trade mode sim|live
    requested = parts[2].lower()
    if requested not in {"sim", "live"}:
        await message.answer(
            "Некорректный режим. Используйте:\n"
            "/trade mode sim\n"
            "/trade mode live"
        )
        return

    current = get_trade_mode()
    logger.info("[trade_mode] /trade mode requested=%s current=%s", requested, current)

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
    logger.info("[trade_mode] waiting PIN chat_id=%s requested=%s", chat_id, requested)

    if requested == "live":
        requested_text = "live (боевой режим)"
    else:
        requested_text = "sim (режим симуляции)"

    await message.answer(
        f"Текущий режим торговли: **{current}**.\n\n"
        f"Вы запросили переключение в режим **{requested_text}**.\n\n"
        "Для подтверждения введите админский PIN одним сообщением.\n"
        "Без правильного PIN режим торговли не изменится."
    )
