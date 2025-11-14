import os
from typing import Dict, Any

from aiogram import Router, types
from aiogram.filters import Command

from scheduler import load_config, save_config, _log_event

router = Router()


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
    cfg: Dict[str, Any] = load_config()

    if len(parts) == 1:
        status = "ON" if cfg.get("status") else "OFF"
        period = cfg.get("period")
        publish = cfg.get("publish")
        msg_lines = [
            "Планировщик:",
            f"  status: {status}",
            f"  period: {period} сек",
            f"  publish: каждые {publish} ч",
        ]
        await message.answer("\n".join(msg_lines))
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
        await message.answer(f"Параметр publish обновлён: каждые {value} ч.")
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
