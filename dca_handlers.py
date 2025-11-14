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
    zero_symbol_budget,
)
from dca_models import DCAConfigPerSymbol
from grid_log import log_grid_created, log_grid_manualy_closed

router = Router()


STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)


def _symbol_raw_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}.json"


def get_symbol_min_notional(symbol: str) -> float:
    """Получить minNotional для символа из локального файла SYMBOL.json."""
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


# -----------------------------
# DCA grid settings (2.7)
# -----------------------------
try:
    GRID_DEPTH_UP = int(os.environ.get("GRID_DEPTH_UP", "2"))
except ValueError:
    GRID_DEPTH_UP = 2

try:
    GRID_DEPTH_RANGE = int(os.environ.get("GRID_DEPTH_RANGE", "3"))
except ValueError:
    GRID_DEPTH_RANGE = 3

try:
    GRID_DEPTH_DOWN = int(os.environ.get("GRID_DEPTH_DOWN", "6"))
except ValueError:
    GRID_DEPTH_DOWN = 6

GRID_ANCHOR = os.environ.get("GRID_ANCHOR", "MA").upper()  # MA | PRICE


def _grid_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}_grid.json"


def _load_state_for_symbol(symbol: str) -> dict:
    """Загружает SYMBOLstate.json, если он есть."""
    symbol = (symbol or "").upper()
    path = STORAGE_PATH / f"{symbol}state.json"
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except Exception:
        return {}


def _get_last_price_from_raw(symbol: str) -> float:
    """Пытается взять текущую цену из SYMBOL.json (trading_params)."""
    symbol = (symbol or "").upper()
    path = _symbol_raw_path(symbol)
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return 0.0

    tp = data.get("trading_params") or {}
    # Возможные варианты названия поля
    candidates = [
        tp.get("last_price_f"),
        tp.get("last_price"),
        tp.get("lastPrice"),
    ]
    for val in candidates:
        try:
            if val is None:
                continue
            return float(val)
        except Exception:
            continue
    return 0.0


def _select_anchor_price(symbol: str, state: dict) -> float:
    """Выбор якоря сетки по GRID_ANCHOR (MA или PRICE)."""
    anchor_mode = (GRID_ANCHOR or "MA").upper()
    try:
        ma = float(state.get("MA30") or 0.0)
    except Exception:
        ma = 0.0

    if anchor_mode == "PRICE":
        price = _get_last_price_from_raw(symbol)
        if price > 0:
            return price
        # fallback на MA, если цену не удалось получить
        if ma > 0:
            return ma
        return 0.0

    # По умолчанию и для GRID_ANCHOR == "MA"
    if ma > 0:
        return ma

    # Если MA невалидна — пробуем цену как запасной вариант
    price = _get_last_price_from_raw(symbol)
    if price > 0:
        return price

    return 0.0


def _depth_multiplier_for_mode(market_mode: str) -> int:
    mode = (market_mode or "RANGE").upper()
    if mode == "UP":
        return GRID_DEPTH_UP
    if mode == "DOWN":
        return GRID_DEPTH_DOWN
    return GRID_DEPTH_RANGE


def _build_grid_for_symbol(symbol: str, cfg: DCAConfigPerSymbol, state: dict) -> dict:
    """Строит структуру <SYMBOL>_grid.json на основе state и DCA-конфига."""
    symbol = (symbol or "").upper()
    now_ts = int(time.time())

    tf1 = str(state.get("tf1") or os.environ.get("TF1", "12"))
    tf2 = str(state.get("tf2") or os.environ.get("TF2", "6"))
    market_mode = str(state.get("market_mode") or "RANGE").upper()

    try:
        atr = float(state.get("ATR14") or 0.0)
    except Exception:
        atr = 0.0

    anchor_price = _select_anchor_price(symbol, state)

    if atr <= 0 or anchor_price <= 0:
        raise ValueError("ATR или anchor_price не заданы или некорректны.")

    depth_mult = _depth_multiplier_for_mode(market_mode)
    depth = float(depth_mult) * atr

    levels = int(getattr(cfg, "levels_count", 0) or 0)
    budget = float(getattr(cfg, "budget_usdc", 0.0) or 0.0)

    if levels <= 0 or budget <= 0:
        raise ValueError("Неверные параметры конфига DCA (budget или levels).")

    if levels == 1:
        step = 0.0
    else:
        step = depth / (levels - 1) if depth > 0 else 0.0

    notional_per_level = budget / levels

    current_levels = []
    for idx in range(levels):
        price = anchor_price - idx * step
        if price <= 0:
            price = anchor_price
        qty = notional_per_level / price if price > 0 else 0.0
        current_levels.append(
            {
                "level_index": idx + 1,
                "grid_id": 1,
                "price": round(price, 8),
                "qty": round(qty, 8),
                "notional": round(notional_per_level, 2),
                "filled": False,
                "filled_ts": None,
            }
        )

    created_ts = int(getattr(cfg, "created_ts", now_ts) or now_ts)
    updated_ts = int(getattr(cfg, "updated_ts", now_ts) or now_ts)

    grid = {
        "symbol": symbol,
        "tf1": tf1,
        "tf2": tf2,
        "campaign_start_ts": now_ts,
        "campaign_end_ts": None,
        "config": {
            "symbol": cfg.symbol,
            "enabled": bool(getattr(cfg, "enabled", False)),
            "budget_usdc": budget,
            "levels_count": levels,
            "base_tf": getattr(cfg, "base_tf", None),
            "created_ts": created_ts,
            "updated_ts": updated_ts,
        },
        "total_levels": levels,
        "filled_levels": 0,
        "spent_usdc": 0.0,
        "current_grid_id": 1,
        "current_market_mode": market_mode,
        "current_anchor_price": anchor_price,
        "current_atr_tf1": atr,
        "current_depth_cycle": depth,
        "current_levels": current_levels,
        "created_ts": now_ts,
        "updated_ts": now_ts,
    }
    return grid


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


    # /dca start <symbol> — построить виртуальную сетку и сохранить <SYMBOL>_grid.json
    if cmd == "start":
        if len(parts) < 3:
            await message.answer("Использование: /dca start <symbol>")
            return

        symbol = parts[2].upper()
        cfg = get_symbol_config(symbol)
        if cfg is None:
            await message.answer(f"DCA: конфиг для {symbol} не найден. Сначала задайте его через /dca set.")
            return

        state = _load_state_for_symbol(symbol)
        if not state:
            await message.answer(
                f"DCA: state для {symbol} не найден. Сначала выполните /now и /market для этой пары."
            )
            return

        try:
            grid = _build_grid_for_symbol(symbol, cfg, state)
        except ValueError as e:
            await message.answer(f"DCA: не удалось построить сетку для {symbol}: {e}")
            return

        gpath = _grid_path(symbol)
        try:
            gpath.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            with gpath.open("w", encoding="utf-8") as f:
                json.dump(grid, f, ensure_ascii=False, indent=2)
        except Exception as e:
            await message.answer(f"DCA: не удалось сохранить файл сетки для {symbol}: {e}")
            return

        # Логируем создание сетки
        try:
            log_grid_created(grid)
        except Exception:
            pass

        await message.answer(
            "DCA start выполнен. Сетка создана.\n"
            f"symbol: {grid.get('symbol')}\n"
            f"market_mode: {grid.get('current_market_mode')}\n"
            f"anchor_price: {grid.get('current_anchor_price')}\n"
            f"ATR(TF1): {grid.get('current_atr_tf1')}\n"
            f"depth: {grid.get('current_depth_cycle')}\n"
            f"levels: {grid.get('total_levels')}\n"
            f"budget_usdc: {grid.get('config', {}).get('budget_usdc')}"
        )
        return

    # /dca status <symbol> — показать статус текущей/последней сетки
    if cmd == "status":
        if len(parts) < 3:
            await message.answer("Использование: /dca status <symbol>")
            return

        symbol = parts[2].upper()
        gpath = _grid_path(symbol)
        try:
            raw = gpath.read_text(encoding="utf-8")
            grid = json.loads(raw)
        except Exception:
            await message.answer(f"DCA: сетка для {symbol} не найдена.")
            return

        campaign_start = grid.get("campaign_start_ts")
        campaign_end = grid.get("campaign_end_ts")
        status = "active" if not campaign_end else "stopped"

        msg_lines = [
            f"DCA status для {symbol}:",
            f"  status: {status}",
            f"  campaign_start_ts: {campaign_start}",
            f"  campaign_end_ts: {campaign_end}",
            f"  market_mode: {grid.get('current_market_mode')}",
            f"  tf1/tf2: {grid.get('tf1')}/{grid.get('tf2')}",
            f"  anchor_price: {grid.get('current_anchor_price')}",
            f"  ATR(TF1): {grid.get('current_atr_tf1')}",
            f"  depth: {grid.get('current_depth_cycle')}",
            f"  total_levels: {grid.get('total_levels')}",
            f"  filled_levels: {grid.get('filled_levels')}",
            f"  spent_usdc: {grid.get('spent_usdc')}",
        ]
        await message.answer("\n".join(msg_lines))
        return

    # /dca stop <symbol> — пометить кампанию как завершённую
    if cmd == "stop":
        if len(parts) < 3:
            await message.answer("Использование: /dca stop <symbol>")
            return

        symbol = parts[2].upper()
        gpath = _grid_path(symbol)
        try:
            raw = gpath.read_text(encoding="utf-8")
            grid = json.loads(raw)
        except Exception:
            await message.answer(f"DCA: сетка для {symbol} не найдена.")
            return

        if grid.get("campaign_end_ts"):
            await message.answer(f"DCA: кампания для {symbol} уже завершена.")
            return

        now_ts = int(time.time())
        grid["campaign_end_ts"] = now_ts
        grid["updated_ts"] = now_ts

        try:
            with gpath.open("w", encoding="utf-8") as f:
                json.dump(grid, f, ensure_ascii=False, indent=2)
        except Exception as e:
            await message.answer(f"DCA: не удалось обновить файл сетки для {symbol}: {e}")
            return

        # Обнуляем бюджет в DCA-конфиге для безопасности
        try:
            zero_symbol_budget(symbol)
        except Exception:
            pass

        # Логируем ручное закрытие кампании
        try:
            log_grid_manualy_closed(grid)
        except Exception:
            pass

        await message.answer(f"DCA: кампания для {symbol} остановлена.")
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
