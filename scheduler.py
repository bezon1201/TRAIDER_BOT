
import os
import json
import time
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from aiogram import Router, types
from aiogram.filters import Command

from metrics import load_symbols, update_symbol_raw, append_raw_market_line
from coin_state import MARKET_PUBLISH, recalc_state_for_symbol, _state_path

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)

router = Router()

CONFIG_PATH = STORAGE_PATH / "sheduler_confyg.json"
LOG_PATH = STORAGE_PATH / "scheduler.jsonl"

# Глобальная задача планировщика
_scheduler_task: Optional[asyncio.Task] = None


def _log_event(payload: Dict[str, Any]) -> None:
    """Пишем строку JSON в scheduler.jsonl."""
    rec = dict(payload)
    rec.setdefault("ts", int(time.time()))
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        # Лог планировщика не критичен для работы
        pass


def _default_config() -> Dict[str, Any]:
    return {
        "status": True,
        "period": 900,
        "publish": MARKET_PUBLISH,
        "last_publish_ts": 0,
    }


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        cfg = _default_config()
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with CONFIG_PATH.open("w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        _log_event({"event": "config_created", **cfg})
        return cfg

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = _default_config()
    # Гарантируем обязательные поля
    for k, v in _default_config().items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


@router.message(Command("scheduler"))
async def cmd_scheduler(message: types.Message) -> None:
    """
    Управление планировщиком.

    /scheduler                 — показать текущие настройки.
    /scheduler period XXX      — задать период в сек (60–21600).
    /scheduler publish XXX     — задать период publish в часах (1–168).
    /scheduler on              — включить планировщик.
    /scheduler off             — выключить планировщик.
    """
    text = (message.text or "").strip()
    parts = text.split()
    cfg = load_config()

    if len(parts) == 1:
        status = "включен" if cfg.get("status") else "выключен"
        period = int(cfg.get("period", 900))
        publish = int(cfg.get("publish", MARKET_PUBLISH))
        await message.answer(
            f"Планировщик {status}.\n"
            f"period = {period} сек\n"
            f"publish = {publish} ч"
        )
        return

    if len(parts) >= 2:
        sub = parts[1].lower()
    else:
        sub = ""

    if sub == "period" and len(parts) >= 3:
        try:
            value = int(parts[2])
        except ValueError:
            await message.answer("Некорректное значение period. Нужна целочисленная секунда.")
            return
        if value < 60 or value > 21600:
            await message.answer("Период должен быть от 60 до 21600 секунд.")
            return
        old = cfg.get("period")
        cfg["period"] = value
        save_config(cfg)
        _log_event({"event": "config_update", "field": "period", "old": old, "new": value})
        await message.answer(f"Период обновлён: {value} сек.")
        return

    if sub == "publish" and len(parts) >= 3:
        try:
            value = int(parts[2])
        except ValueError:
            await message.answer("Некорректное значение publish. Нужны целые часы.")
            return
        if value < 1 or value > 168:
            await message.answer("Параметр publish должен быть от 1 до 168 часов.")
            return
        old = cfg.get("publish")
        cfg["publish"] = value
        save_config(cfg)
        _log_event({"event": "config_update", "field": "publish", "old": old, "new": value})
        await message.answer(f"Период publish обновлён: {value} ч.")
        return

    if sub == "on":
        old = bool(cfg.get("status"))
        cfg["status"] = True
        save_config(cfg)
        _log_event({"event": "config_update", "field": "status", "old": old, "new": True})
        await message.answer("Планировщик включен.")
        return

    if sub == "off":
        old = bool(cfg.get("status"))
        cfg["status"] = False
        save_config(cfg)
        _log_event({"event": "config_update", "field": "status", "old": old, "new": False})
        await message.answer("Планировщик выключен.")
        return

    await message.answer(
        "Неизвестная подкоманда для /scheduler. Доступно: "
        "/scheduler, /scheduler period XXX, /scheduler publish XXX, /scheduler on, /scheduler off."
    )


async def _step1_collect_raw(logger: logging.Logger) -> int:
    """
    Шаг 1: раз в period секунд вызываем аналог /now для всех пар (без сообщений в Телеграм).

    Возвращает количество обновлённых пар.
    """
    symbols = load_symbols()
    if not symbols:
        logger.info("[scheduler] step1: symbols_list пуст.")
        _log_event({"event": "step1_now_all", "symbols": 0})
        return 0

    updated = 0
    async with aiohttp.ClientSession() as session:
        for sym in symbols:
            try:
                info = await update_symbol_raw(session, sym)
                append_raw_market_line(sym, info)
                updated += 1
            except Exception:
                logger.exception("[scheduler] Ошибка обновления сырья для %s", sym)
                _log_event({"event": "error_step1_symbol", "symbol": sym})

    _log_event({"event": "step1_now_all", "symbols": updated})
    logger.info("[scheduler] step1: обновили сырьё для %s пар.", updated)
    return updated


def _load_old_market_mode(symbol: str) -> str:
    """Возвращает старый режим рынка из SYMBOLstate.json, либо пустую строку, если файла нет/повреждён."""
    path = _state_path(symbol)
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ""
    val = data.get("market_mode")
    return str(val).upper() if val is not None else ""


async def _step2_market_force_all(bot: "Bot", admin_chat_id: int, logger: logging.Logger) -> Dict[str, Any]:
    """
    Шаг 2: раз в publish часов пересчитываем state для всех пар (аналог /market force без ответов в чат).

    Перед записью сравниваем старый и новый market_mode и по итогам шлём админу:
    1) если изменений нет:  "Рынок пересчитан. Изменений нет";
    2) если есть изменения: перечисляем их.
    """
    from aiogram import Bot  # импорт для type hints и во избежание циклических зависимостей

    symbols = load_symbols()
    if not symbols:
        logger.info("[scheduler] step2: symbols_list пуст.")
        _log_event({"event": "step2_market_force_all", "symbols": 0, "changes": 0})
        return {"symbols": 0, "changes": 0}

    now_ts = int(time.time())
    changes: List[Dict[str, str]] = []
    total = 0

    for sym in symbols:
        sym_up = (sym or "").upper()
        if not sym_up:
            continue
        total += 1
        old_mode = _load_old_market_mode(sym_up)
        state = recalc_state_for_symbol(sym_up, now_ts=now_ts)
        new_mode = str(state.get("market_mode", "RANGE")).upper()

        if old_mode != new_mode:
            changes.append(
                {
                    "symbol": sym_up,
                    "old": old_mode or "-",
                    "new": new_mode,
                }
            )

    changed_count = len(changes)
    _log_event(
        {
            "event": "step2_market_force_all",
            "symbols": total,
            "changes": changed_count,
        }
    )
    logger.info(
        "[scheduler] step2: пересчитали state для %s пар, изменений: %s",
        total,
        changed_count,
    )

    # Уведомляем админа
    if changed_count == 0:
        text = "Рынок пересчитан. Изменений нет."
    else:
        lines = ["Рынок пересчитан. Изменения:"]
        for ch in changes:
            lines.append(f"{ch['symbol']}: {ch['old']} -> {ch['new']}")
        text = "\n".join(lines)

    try:
        await bot.send_message(chat_id=admin_chat_id, text=text)
    except Exception:
        logger.exception("[scheduler] Не удалось отправить уведомление админу.")

    return {"symbols": total, "changes": changed_count}


async def _scheduler_loop(bot: "Bot", admin_chat_id: int, logger: logging.Logger) -> None:
    """Основной цикл планировщика."""
    _log_event({"event": "scheduler_started"})
    logger.info("[scheduler] Планировщик запущен.")
    while True:
        cfg = load_config()
        status = bool(cfg.get("status", True))
        period = int(cfg.get("period", 900) or 900)
        publish = int(cfg.get("publish", MARKET_PUBLISH) or MARKET_PUBLISH)
        last_publish_ts = int(cfg.get("last_publish_ts", 0) or 0)

        # Нормируем period/publish в допустимый диапазон
        if period < 60:
            period = 60
        if period > 21600:
            period = 21600
        if publish < 1:
            publish = 1
        if publish > 168:
            publish = 168

        if not status:
            logger.info("[scheduler] status=false, ждем %s сек.", period)
            await asyncio.sleep(period)
            continue

        # Шаг 1: обновление сырья (аналог /now)
        try:
            await _step1_collect_raw(logger)
        except Exception:
            logger.exception("[scheduler] Ошибка в шаге 1 (collect raw).")
            _log_event({"event": "error_step1"})

        now_ts = int(time.time())
        need_publish = now_ts - last_publish_ts >= publish * 3600

        # Шаг 2: пересчёт рынка (аналог /market force)
        if need_publish:
            try:
                res = await _step2_market_force_all(bot, admin_chat_id, logger)
                cfg["last_publish_ts"] = now_ts
                save_config(cfg)
                _log_event(
                    {
                        "event": "publish_done",
                        "symbols": res.get("symbols", 0),
                        "changes": res.get("changes", 0),
                        "publish_hours": publish,
                    }
                )
            except Exception:
                logger.exception("[scheduler] Ошибка в шаге 2 (market force).")
                _log_event({"event": "error_step2"})

        await asyncio.sleep(period)


def start_scheduler(bot: "Bot", admin_chat_id: int, logger: Optional[logging.Logger] = None) -> None:
    """
    Запускает фоновую задачу планировщика.

    Вызывать один раз при старте приложения.
    """
    from aiogram import Bot  # noqa: F401

    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return

    if logger is None:
        logger = logging.getLogger(__name__)

    loop = asyncio.get_event_loop()
    _scheduler_task = loop.create_task(_scheduler_loop(bot, admin_chat_id, logger))
    logger.info("[scheduler] Фоновая задача планировщика создана.")
