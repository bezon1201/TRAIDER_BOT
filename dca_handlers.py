import os
import json
import time
from pathlib import Path

from aiogram import Router, types
from aiogram.filters import Command

from dca_config import (
    load_dca_config,
    save_dca_config,
    get_symbol_config,
    upsert_symbol_config,
    validate_budget_vs_min_notional,
)
from dca_models import DCAConfigPerSymbol

router = Router()

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)


def _symbol_raw_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}.json"

def get_symbol_min_notional(symbol: str) -> float:
    """
    Получить minNotional для символа из локального файла SYMBOL.json.

    Используем данные, которые уже сохраняет команда /now:
    - trading_params.symbol_info.min_notional (float)
    - либо filters.NOTIONAL.minNotional (строка), если нужно.
    """
    symbol = symbol.upper()
    path = _symbol_raw_path(symbol)
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return 0.0

    tp = data.get("trading_params") or {}
    # Сначала пробуем удобный дублированный float
    si = tp.get("symbol_info") or {}
    min_not = si.get("min_notional")
    if isinstance(min_not, (int, float)):
        try:
            return float(min_not)
        except Exception:
            pass

    # Если не получилось — пробуем исходный фильтр NOTIONAL
    filters = tp.get("filters") or {}
    notional_f = filters.get("NOTIONAL") or {}
    try:
        return float(notional_f.get("minNotional", 0))
    except Exception:
        return 0.0

    if tf.isdigit():
        return f"{tf}h"
    return tf

@router.message(Command("dca"))
async def cmd_dca(message: types.Message) -> None:
    """
    Базовые команды управления конфигурацией DCA.

    Синтаксис (черновой, будет дорабатываться под клавиатуру):
    /dca
        — краткий статус по конфигам DCA.
    /dca list
        — список конфигов по всем символам.
    /dca cfg <symbol>
        — подробный конфиг по одной паре + проверка minNotional.
    /dca set <symbol> budget <USDC>
        — задать/обновить месячный бюджет для пары.
    /dca set <symbol> levels <N>
        — задать/обновить количество уровней в сетке.
    /dca on <symbol> / /dca off <symbol>
        — включить/выключить использование пары в DCA.
    """
    text = (message.text or "").strip()
    parts = text.split()

    # Просто /dca — краткий статус
    if len(parts) == 1:
        cfgs = load_dca_config()
        total = len(cfgs)
        enabled = sum(1 for c in cfgs.values() if c.enabled)
        if total == 0:
            await message.answer(
                "DCA: конфиги отсутствуют. "
                "Сначала задайте хотя бы один конфиг командой вида:\n"
                "/dca set BNBUSDC budget 300\n"
                "/dca set BNBUSDC levels 10"
            )
            return
        await message.answer(
            f"DCA-конфиги: всего {total}, включено {enabled}.\n"
            "Подробнее: /dca list или /dca cfg <symbol>."
        )
        return

    cmd = parts[1].lower()

    # /dca list
    if cmd in {"list", "ls"}:
        cfgs = load_dca_config()
        if not cfgs:
            await message.answer("DCA: пока нет ни одного сохранённого конфига.")
            return

        lines = ["Список DCA-конфигов:"]
        for symbol in sorted(cfgs.keys()):
            cfg = cfgs[symbol]
            min_not = get_symbol_min_notional(symbol)
            note = ""
            if min_not > 0:
                ok, err = validate_budget_vs_min_notional(cfg, min_not)
                if ok:
                    note = "OK"
                else:
                    note = "ERR"
            else:
                note = "minNotional неизвестен"

            status = "ON" if cfg.enabled else "OFF"
            lines.append(
                f"{symbol}: {status}, budget={cfg.budget_usdc}, "
                f"levels={cfg.levels_count}, check={note}"
            )

        await message.answer("\n".join(lines))
        return

    # /dca cfg <symbol>
    if cmd in {"cfg", "config"}:
        if len(parts) < 3:
            await message.answer("Использование: /dca cfg <symbol>")
            return
        symbol = parts[2].upper()
        cfg = get_symbol_config(symbol)
        if cfg is None:
            await message.answer(f"DCA: конфиг для {symbol} не найден.")
            return

        min_not = get_symbol_min_notional(symbol)
        details = [
            f"Конфиг DCA для {symbol}:",
            f"  enabled: {cfg.enabled}",
            f"  budget_usdc: {cfg.budget_usdc}",
            f"  levels_count: {cfg.levels_count}",
            f"  base_tf: {cfg.base_tf or '-'}",
        ]

        if min_not > 0:
            ok, err = validate_budget_vs_min_notional(cfg, min_not)
            details.append(f"  minNotional: {min_not}")
            if ok:
                details.append("  Валидация бюджета: OK.")
            else:
                details.append(f"  Валидация бюджета: {err}")
        else:
            details.append(
                "  minNotional: неизвестен. Сначала выполните /now для этой пары,"
                " чтобы собрать торговые параметры."
            )

        await message.answer("\n".join(details))
        return

    # /dca set <symbol> budget <USDC> | /dca set <symbol> levels <N>
    if cmd == "set":
        if len(parts) < 5:
            await message.answer(
                "Использование:\n"
                "/dca set <symbol> budget <USDC>\n"
                "/dca set <symbol> levels <N>"
            )
            return
        symbol = parts[2].upper()
        field = parts[3].lower()
        value = parts[4]

        cfg = get_symbol_config(symbol)
        now_ts = int(time.time())
        if cfg is None:
            cfg = DCAConfigPerSymbol(symbol=symbol, created_ts=now_ts)
        cfg.updated_ts = now_ts

        if field == "budget":
            try:
                cfg.budget_usdc = float(value.replace(",", "."))
            except Exception:
                await message.answer("Не удалось разобрать значение budget_usdc. Ожидается число.")
                return
        elif field == "levels":
            try:
                lv = int(value)
            except Exception:
                await message.answer("Не удалось разобрать levels_count. Ожидается целое число.")
                return
            if lv <= 0:
                await message.answer("levels_count должен быть положительным.")
                return
            cfg.levels_count = lv
        else:
            await message.answer("Поддерживаются только поля budget и levels.")
            return

        upsert_symbol_config(cfg)

        min_not = get_symbol_min_notional(symbol)
        msg_lines = [
            f"DCA-конфиг для {symbol} обновлён:",
            f"  enabled: {cfg.enabled}",
            f"  budget_usdc: {cfg.budget_usdc}",
            f"  levels_count: {cfg.levels_count}",
        ]
        if min_not > 0:
            ok, err = validate_budget_vs_min_notional(cfg, min_not)
            msg_lines.append(f"  minNotional: {min_not}")
            if ok:
                msg_lines.append("  Валидация бюджета: OK.")
            else:
                msg_lines.append(f"  Валидация бюджета: {err}")
        else:
            msg_lines.append(
                "  minNotional: неизвестен (нет локальных данных по фильтрам Binance). "
                "Сначала выполните /now для этой пары."
            )

        await message.answer("\n".join(msg_lines))
        return

    # /dca on <symbol> / /dca off <symbol>
    if cmd in {"on", "off"}:
        if len(parts) < 3:
            await message.answer(f"Использование: /dca {cmd} <symbol>")
            return
        symbol = parts[2].upper()
        cfg = get_symbol_config(symbol)
        if cfg is None:
            # если конфига нет, создадим заготовку с нулевыми значениями
            now_ts = int(time.time())
            cfg = DCAConfigPerSymbol(symbol=symbol, created_ts=now_ts, updated_ts=now_ts)
        cfg.enabled = (cmd == "on")
        cfg.updated_ts = int(time.time())
        upsert_symbol_config(cfg)
        state = "включён" if cfg.enabled else "выключен"
        await message.answer(f"DCA для {symbol} {state}.")
        return

    # Если подкоманда не распознана
    await message.answer(
        "Неизвестная подкоманда для /dca. Доступно:\n"
        "/dca\n"
        "/dca list\n"
        "/dca cfg <symbol>\n"
        "/dca set <symbol> budget <USDC>\n"
        "/dca set <symbol> levels <N>\n"
        "/dca on <symbol>\n"
        "/dca off <symbol>"
    )

