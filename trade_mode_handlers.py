import os
from typing import Dict

from aiogram import Router, types

from trade_mode import get_trade_mode, set_trade_mode

router = Router()

ADMIN_KEY = os.environ.get("ADMIN_KEY", "").strip()

# Для личного бота достаточно отслеживать ожидание PIN по chat_id.
_pending_mode_by_chat: Dict[int, str] = {}


@router.message()
async def handle_trade(message: types.Message) -> None:
    """Обработка команд /trade mode и ввода PIN.

    Реализует:
    - /trade mode
    - /trade mode sim
    - /trade mode live
    - ожидание PIN и его проверку
    - отмену перехода по любой команде.
    """
    if not message.text:
        return

    text = message.text.strip()
    chat_id = message.chat.id

    # Если уже ждём PIN для этого чата — обрабатываем сначала этот сценарий.
    if chat_id in _pending_mode_by_chat:
        # Любая команда (начинается с /) отменяет переход.
        if text.startswith("/"):
            _pending_mode_by_chat.pop(chat_id, None)
            await message.answer("Запрос смены режима торговли отменён.")
            # Если это не /trade, дальше ничего не делаем — команду обработают другие хендлеры.
            if not text.startswith("/trade"):
                return
            # Если это /trade ..., продолжаем ниже как с обычной командой.
        else:
            # Считаем это попыткой ввода PIN.
            requested = _pending_mode_by_chat.pop(chat_id, None)
            if requested is None:
                return

            if not ADMIN_KEY:
                await message.answer(
                    "Смена режима торговли недоступна: переменная окружения ADMIN_KEY не задана."
                )
                return

            if text != ADMIN_KEY:
                await message.answer("Неверный PIN, режим торговли не изменён.")
                return

            old_mode = get_trade_mode()
            try:
                set_trade_mode(requested)
            except Exception:
                await message.answer("Ошибка при смене режима торговли. Попробуйте ещё раз.")
                return

            if old_mode == "live":
                descr_old = "live (боевой режим)"
            else:
                descr_old = "sim (симуляция)"

            if requested == "live":
                descr_new = "live (боевой режим)"
            else:
                descr_new = "sim (симуляция)"

            await message.answer(
                f"Режим торговли изменён: **{descr_old} → {descr_new}**.\n\n"
                f"Текущий режим: **{descr_new}**."
            )
            return

    # На этом этапе либо нет ожидания PIN, либо мы его только что отменили.
    if not text.startswith("/trade"):
        return

    parts = text.split()

    # Ждём минимум: /trade mode
    if len(parts) < 2 or parts[1].lower() != "mode":
        await message.answer(
            "Использование:\n"
            "/trade mode — показать текущий режим торговли.\n"
            "/trade mode sim — запросить режим симуляции (через PIN).\n"
            "/trade mode live — запросить боевой режим (через PIN)."
        )
        return

    # /trade mode — просто показать текущий режим
    if len(parts) == 2:
        current = get_trade_mode()
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

    # /trade mode <режим>
    requested = parts[2].lower()

    if requested not in {"sim", "live"}:
        await message.answer(
            "Некорректный режим. Используйте:\n"
            "/trade mode sim\n"
            "/trade mode live"
        )
        return

    current = get_trade_mode()
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
        await message.answer(
            "Смена режима торговли недоступна: переменная окружения ADMIN_KEY не задана."
        )
        return

    # Запоминаем, что для этого чата ждём PIN.
    _pending_mode_by_chat[chat_id] = requested

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
