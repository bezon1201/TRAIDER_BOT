"""Handlers for ORDERS-related callbacks (order click / confirm / cancel).

Выделено из handlers.py, чтобы уменьшить размер и сложность menu_callback.
"""

from __future__ import annotations

import logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from dca_orders import load_orders, execute_virtual_market_buy, activate_virtual_limit_buy

log = logging.getLogger(__name__)


async def handle_order_confirm(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    query,
    data: str,
    safe_answer_callback,
    safe_delete_message,
    redraw_main_menu_from_user_data,
) -> None:
    parts = data.split(":")
    if len(parts) != 5:
        log.info("ORDERS CONFIRM: некорректный формат callback %s", data)
        await safe_answer_callback(
            query,
            text="ORDERS: действие пока не реализовано.",
            show_alert=False,
        )
        return

    _, _, symbol, grid_id_str, level_index_str = parts
    try:
        grid_id = int(grid_id_str)
        level_index = int(level_index_str)
    except ValueError:
        log.info("ORDERS CONFIRM: некорректные grid_id/level_index в callback %s", data)
        await safe_answer_callback(
            query,
            text="ORDERS: действие пока не реализовано.",
            show_alert=False,
        )
        return

    symbol_u = (symbol or "").upper()
    log.info(
        "ORDERS CONFIRM: подтверждение ордера %s (grid_id=%s, level_index=%s)",
        symbol_u,
        grid_id,
        level_index,
    )

    orders = load_orders(symbol_u)
    if not orders:
        log.info("ORDERS CONFIRM: нет ордеров для %s", symbol_u)
        await safe_answer_callback(
            query,
            text="Ордер не найден (подробнее см. в логе).",
            show_alert=False,
        )
        return

    target = None
    for o in orders:
        if getattr(o, "grid_id", None) == grid_id and getattr(o, "level_index", None) == level_index:
            target = o
            break

    if not target:
        log.info(
            "ORDERS CONFIRM: ордер не найден для %s (grid_id=%s, level_index=%s)",
            symbol_u,
            grid_id,
            level_index,
        )
        await safe_answer_callback(
            query,
            text="Ордер не найден (подробнее см. в логе).",
            show_alert=False,
        )
        return

    status = getattr(target, "status", "NEW") or "NEW"
    if status == "FILLED":
        await safe_delete_message(context, query.message.chat_id, query.message.message_id)
        await safe_answer_callback(
            query,
            text="Ордер уже исполнен.",
            show_alert=False,
        )
        return

    if status == "ACTIVE":
        await safe_delete_message(context, query.message.chat_id, query.message.message_id)
        await safe_answer_callback(
            query,
            text="Ордер уже активен.",
            show_alert=False,
        )
        return

    if status not in ("NEW", "CANCELED"):
        await safe_delete_message(context, query.message.chat_id, query.message.message_id)
        await safe_answer_callback(
            query,
            text="Ордер недоступен для выполнения.",
            show_alert=False,
        )
        return

    order_type = getattr(target, "order_type", "LIMIT_BUY") or "LIMIT_BUY"

    # MARKET BUY: полное виртуальное исполнение
    if order_type == "MARKET_BUY":
        log.info(
            "ORDERS CONFIRM: MARKET BUY для %s (grid_id=%s, level_index=%s)",
            symbol_u,
            grid_id,
            level_index,
        )
        last_price = get_symbol_last_price_light(symbol_u)
        if not last_price or last_price <= 0:
            log.warning(
                "ORDERS CONFIRM: не удалось получить цену с Binance для %s при подтверждении MARKET",
                symbol_u,
            )
            await safe_answer_callback(
                query,
                text="Не удалось получить цену с Binance",
                show_alert=False,
            )
            return

        vorder = execute_virtual_market_buy(
            symbol_u,
            grid_id,
            level_index,
            execution_price=last_price,
            commission=0.0,
            reason="manual",
        )
        if not vorder:
            await safe_answer_callback(
                query,
                text="Не удалось исполнить маркет-ордер (подробнее см. в логе).",
                show_alert=False,
            )
            return

        await safe_delete_message(context, query.message.chat_id, query.message.message_id)
        await safe_answer_callback(
            query,
            text="Маркет-ордер исполнен.",
            show_alert=False,
        )
        await redraw_main_menu_from_user_data(context)
        return

    # LIMIT BUY: переводим ордер в ACTIVE
    if order_type == "LIMIT_BUY":
        log.info(
            "ORDERS CONFIRM: LIMIT BUY для %s (grid_id=%s, level_index=%s)",
            symbol_u,
            grid_id,
            level_index,
        )

        last_price = get_symbol_last_price_light(symbol_u)
        if not last_price or last_price <= 0:
            log.warning(
                "ORDERS CONFIRM: не удалось получить цену с Binance для %s при подтверждении LIMIT",
                symbol_u,
            )
            await safe_answer_callback(
                query,
                text="Не удалось получить цену с Binance",
                show_alert=False,
            )
            return

        vorder_limit = activate_virtual_limit_buy(
            symbol_u,
            grid_id,
            level_index,
            reason="manual",
        )
        if not vorder_limit:
            await safe_answer_callback(
                query,
                text="Не удалось активировать лимитный ордер (подробнее см. в логе).",
                show_alert=False,
            )
            # Сообщение-подтверждение оставляем, чтобы пользователь мог повторить попытку или отменить.
            return

        await safe_delete_message(context, query.message.chat_id, query.message.message_id)
        await safe_answer_callback(
            query,
            text="Лимитный ордер отправлен.",
            show_alert=False,
        )
        await redraw_main_menu_from_user_data(context)
        return

    # Неподдерживаемый тип ордера
    log.warning(
        "ORDERS CONFIRM: неподдерживаемый тип ордера %s для %s (grid_id=%s, level_index=%s)",
        order_type,
        symbol_u,
        grid_id,
        level_index,
    )
    await safe_answer_callback(
        query,
        text="ORDERS: действие пока не реализовано.",
        show_alert=False,
    )
    return

    # Отмена диалога по ордеру (кнопка ❌)
    

async def handle_order_cancel_dialog(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    query,
    data: str,
    safe_answer_callback,
    safe_delete_message,
    redraw_main_menu_from_user_data,
) -> None:
    parts = data.split(":")
    if len(parts) != 5:
        log.info("ORDERS CANCEL: некорректный формат callback %s", data)
        await safe_answer_callback(
            query,
            text="Действие отменено.",
            show_alert=False,
        )
        return

    # Просто удаляем сообщение-подтверждение, состояние ордеров не меняем.
    await safe_delete_message(context, query.message.chat_id, query.message.message_id)
    await safe_answer_callback(
        query,
        text="Действие отменено.",
        show_alert=False,
    )
    return

    # Клик по строке ордера — открываем диалог подтверждения
    

async def handle_order_click(
    update,
    context: ContextTypes.DEFAULT_TYPE,
    query,
    data: str,
    safe_answer_callback,
    safe_delete_message,
    redraw_main_menu_from_user_data,
) -> None:
    parts = data.split(":")
    if len(parts) != 4:
        log.info("ORDERS: неизвестный формат callback %s", data)
        await safe_answer_callback(
            query,
            text="ORDERS: действие пока не реализовано.",
            show_alert=False,
        )
        return

    _, symbol, grid_id_str, level_index_str = parts
    try:
        grid_id = int(grid_id_str)
        level_index = int(level_index_str)
    except ValueError:
        log.warning(
            "ORDERS: не удалось распарсить grid_id/level_index из %s",
            data,
        )
        await safe_answer_callback(
            query,
            text="ORDERS: действие пока не реализовано.",
            show_alert=False,
        )
        return

    orders = load_orders(symbol)
    target = None
    for o in orders:
        if getattr(o, "grid_id", None) == grid_id and getattr(o, "level_index", None) == level_index:
            target = o
            break

    if not target:
        log.info(
            "ORDERS: ордер не найден для %s (grid_id=%s, level_index=%s)",
            symbol,
            grid_id,
            level_index,
        )
        await safe_answer_callback(
            query,
            text="Ордер не найден (возможно, сетка обновлена).",
            show_alert=False,
        )
        return

    status = getattr(target, "status", "NEW") or "NEW"
    if status == "FILLED":
        await safe_answer_callback(
            query,
            text="Ордер уже исполнен.",
            show_alert=False,
        )
        return
    if status == "ACTIVE":
        await safe_answer_callback(
            query,
            text="Ордер уже активен.",
            show_alert=False,
        )
        return

    order_type = getattr(target, "order_type", "LIMIT_BUY") or "LIMIT_BUY"
    try:
        preview_price = get_symbol_last_price_light(symbol)
    except Exception as e:  # noqa: BLE001
        log.exception(
            "ORDERS: ошибка при получении preview-цены для %s: %s",
            symbol,
            e,
        )
        preview_price = None

    if not preview_price or preview_price <= 0:
        await safe_answer_callback(
            query,
            text="Не удалось получить цену с Binance",
            show_alert=False,
        )
        return

    # Форматируем числа для сообщения
    try:
        quote_qty = float(getattr(target, "quote_qty", 0.0) or 0.0)
    except (TypeError, ValueError):
        quote_qty = 0.0

    if quote_qty == int(quote_qty):
        quote_str_val = str(int(quote_qty))
    else:
        quote_str_val = f"{quote_qty:.2f}".rstrip("0").rstrip(".")
    quote_str = f"{quote_str_val} USDC"

    price_int = int(preview_price) if preview_price > 0 else 0
    price_str = f"{price_int:,}".replace(",", " ") + "$"

    if order_type == "MARKET_BUY":
        text = (
            f"Отправить Market Buy order на сумму {quote_str} "
            f"по цене {price_str} для {symbol}?"
        )
    else:
        # Для лимитного ордера показываем лимитную цену и текущую рыночную
        level_price = float(getattr(target, "price", 0.0) or 0.0)
        level_price_int = int(level_price) if level_price > 0 else 0
        level_price_str = f"{level_price_int:,}".replace(",", " ") + "$"
        text = (
            f"Отправить Limit Buy order на сумму {quote_str} "
            f"по цене {level_price_str} для {symbol}?\n"
            f"(текущая цена {price_str})"
        )

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"order:confirm:{symbol}:{grid_id}:{level_index}",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"order:cancel:{symbol}:{grid_id}:{level_index}",
                ),
            ]
        ]
    )
    chat_id = query.message.chat_id
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
    await safe_answer_callback(query)
    return

    # Пока остальные действия ORDERS (массовые) — заглушки, чтобы callback не зависал