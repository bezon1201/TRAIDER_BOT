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
from trade_mode import get_trade_mode, is_sim_mode, is_live_mode
from dca_models import DCAConfigPerSymbol
from grid_log import log_grid_created, log_grid_rolled, log_grid_manualy_closed
from dca_status import build_dca_status_text

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


def _has_active_campaign(symbol: str) -> bool:
    """
    Проверяет, есть ли для symbol активная DCA-кампания
    (файл <SYMBOL>_grid.json с campaign_end_ts == None/0).
    """
    symbol = (symbol or "").upper()
    if not symbol:
        return False
    gpath = _grid_path(symbol)
    if not gpath.exists():
        return False
    try:
        raw = gpath.read_text(encoding="utf-8")
        grid = json.loads(raw)
    except Exception:
        return False
    if grid.get("campaign_end_ts"):
        return False
    return True




def _format_ts_date(ts: int | None) -> str:
    try:
        if not ts:
            return "--"
        ts_int = int(ts)
        if ts_int <= 0:
            return "--"
        dt = time.localtime(ts_int)
        return f"{dt.tm_mday:02d}-{dt.tm_mon:02d}-{dt.tm_year}"
    except Exception:
        return "--"


def _format_money(value, digits: int = 2) -> str:
    try:
        v = float(value)
    except Exception:
        v = 0.0
    return f"{v:.{digits}f}$"




def _build_status_lines_from_grid(grid: dict) -> list[str]:
    symbol = str(grid.get("symbol") or "UNKNOWN").upper()

    campaign_start = grid.get("campaign_start_ts")
    campaign_end = grid.get("campaign_end_ts")

    status = "active" if not campaign_end else "stopped"
    grid_id = grid.get("current_grid_id", 1)

    start_str = _format_ts_date(campaign_start)
    if campaign_end:
        reason = str(grid.get("closed_reason") or "").lower()
        if reason == "budget":
            reason_text = "Budget close"
        elif reason == "levels":
            reason_text = "Level close"
        elif reason == "manual":
            reason_text = "Manual stop"
        else:
            # Для старых кампаний без поля closed_reason сохраняем старое поведение
            reason_text = "Manual stop"
        stop_str = f"Stop: {_format_ts_date(campaign_end)} {reason_text}"
    else:
        stop_str = "Stop: -- (active)"

    market_mode = str(grid.get("current_market_mode") or "RANGE").upper()
    tf1 = grid.get("tf1")
    tf2 = grid.get("tf2")

    anchor_price = grid.get("current_anchor_price")
    atr_tf1 = grid.get("current_atr_tf1")
    depth_cycle = grid.get("current_depth_cycle")

    total_levels = grid.get("total_levels")
    filled_levels = grid.get("filled_levels")
    spent_usdc = grid.get("spent_usdc")

    cfg = grid.get("config") or {}
    budget_usdc = cfg.get("budget_usdc")

    lines = [
        f"{symbol} {status} Grid id: {grid_id}",
        f"Start: {start_str}",
        stop_str,
        f"Market: {market_mode} tf1/tf2: {tf1}/{tf2}",
        f"Anchor: {_format_money(anchor_price)} ATR: {_format_money(atr_tf1)} Depth: {_format_money(depth_cycle)}",
        f"Total levels: {total_levels} Filled levels: {filled_levels}",
        f"Budget: {_format_money(budget_usdc)}",
        f"Spent: {_format_money(spent_usdc)}",
    ]
    return lines



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
        "remaining_levels": levels,
        "spent_usdc": 0.0,
        "avg_price": None,
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


    # /dca simulate <symbol> [bars]
    if cmd in {"simulate", "sim"}:
        if len(parts) < 3:
            await message.answer("Использование: /dca simulate <symbol> [bars]")
            return

        symbol = parts[2].upper()
        # bars по умолчанию
        bars_requested = 100
        if len(parts) >= 4:
            try:
                bars_requested = int(parts[3])
            except ValueError:
                await message.answer("DCA simulate: bars должен быть целым числом > 0.")
                return
        if bars_requested <= 0:
            await message.answer("DCA simulate: bars должен быть > 0.")
            return

        # ограничиваем сверху, чтобы не улететь по нагрузке
        MAX_SIM_BARS = 1000
        if bars_requested > MAX_SIM_BARS:
            bars_requested = MAX_SIM_BARS

        # Команда доступна только в SIM-режиме
        if not is_sim_mode():
            mode = get_trade_mode()
            mode_str = mode or "unknown"
            await message.answer(
                f"DCA simulate доступна только в режиме SIM. Текущий режим: {mode_str}."
            )
            return

        # Проверяем наличие активной кампании и файла сетки
        gpath = _grid_path(symbol)
        try:
            raw_grid = gpath.read_text(encoding="utf-8")
            grid = json.loads(raw_grid)
        except FileNotFoundError:
            await message.answer(f"DCA simulate: для {symbol} нет файла сетки (<SYMBOL>_grid.json).")
            return
        except Exception:
            await message.answer(f"DCA simulate: не удалось прочитать сетку для {symbol}.")
            return

        if grid.get("campaign_end_ts"):
            await message.answer(
                f"DCA simulate: активная DCA-кампания для {symbol} не найдена (кампания уже завершена)."
            )
            return

        # Загружаем сырые данные по свечам TF1 из <SYMBOL>.json
        rpath = _symbol_raw_path(symbol)
        try:
            raw_data = rpath.read_text(encoding="utf-8")
            data = json.loads(raw_data)
        except FileNotFoundError:
            await message.answer(
                f"DCA simulate: нет файла сырых данных для {symbol} ({symbol}.json). "
                "Сначала выполните /now для этой пары."
            )
            return
        except Exception:
            await message.answer(f"DCA simulate: не удалось прочитать сырые данные для {symbol}.")
            return

        raw = data.get("raw") or {}
        tf1_key = str(grid.get("tf1") or data.get("tf1") or "").strip()
        tf1_block = (raw.get(tf1_key) or {}) if tf1_key else {}
        candles = tf1_block.get("candles") or []

        if not isinstance(candles, list) or not candles:
            await message.answer(
                f"DCA simulate: нет свечей TF1 для {symbol} (ключ TF1='{tf1_key}' пуст или без candles)."
            )
            return

        # фильтруем свечи по времени старта кампании
        start_ts = int(grid.get("campaign_start_ts") or 0)
        if start_ts > 0:
            filtered = [c for c in candles if int(c.get("ts", 0)) >= start_ts]
        else:
            filtered = list(candles)

        if not filtered:
            await message.answer(
                f"DCA simulate: нет свечей TF1 для {symbol} после старта кампании."
            )
            return

        # Берём хвост по количеству, не больше доступного
        total_available = len(filtered)
        bars_to_use = min(bars_requested, total_available)
        bars_for_sim = filtered[-bars_to_use:]

        # Снимок состояния до симуляции
        def _safe_int(v):
            try:
                return int(v)
            except Exception:
                return 0

        def _safe_float(v):
            try:
                return float(v)
            except Exception:
                return 0.0

        init_filled = _safe_int(grid.get("filled_levels"))
        init_total = _safe_int(grid.get("total_levels"))
        init_spent = _safe_float(grid.get("spent_usdc"))
        init_avg = grid.get("avg_price")
        init_end_ts = grid.get("campaign_end_ts")

        # Запускаем движок симуляции по одной свече
        try:
            from grid_sim import simulate_bar_for_symbol  # локальный импорт во избежание циклов
        except Exception:
            await message.answer("DCA simulate: внутренняя ошибка при импорте движка симуляции.")
            return

        applied = 0
        for bar in bars_for_sim:
            try:
                simulate_bar_for_symbol(symbol, bar)
                applied += 1
            except Exception:
                # не падаем из-за одной неудачной свечи
                continue

        # перечитываем актуальный grid после симуляции
        try:
            raw_grid2 = gpath.read_text(encoding="utf-8")
            grid2 = json.loads(raw_grid2)
        except Exception:
            grid2 = grid  # fallback — используем старую версию

        final_filled = _safe_int(grid2.get("filled_levels"))
        final_total = _safe_int(grid2.get("total_levels"))
        final_spent = _safe_float(grid2.get("spent_usdc"))
        final_avg = grid2.get("avg_price")
        final_end_ts = grid2.get("campaign_end_ts")

        cfg = grid2.get("config") or {}
        budget_usdc = _safe_float(cfg.get("budget_usdc"))

        status = "active"
        if final_end_ts:
            closed_by_levels = final_total > 0 and final_filled >= final_total
            closed_by_budget = budget_usdc > 0 and final_spent >= budget_usdc
            if closed_by_levels and closed_by_budget:
                status = "closed_by_budget_and_levels"
            elif closed_by_budget:
                status = "closed_by_budget"
            elif closed_by_levels:
                status = "closed_by_levels"
            else:
                status = "closed"

        lines = []
        lines.append(
            f"DCA simulate для {symbol} завершена. "
            f"Свечей TF1 обработано: {applied} (запрошено: {bars_requested}, доступно: {total_available})."
        )
        lines.append(
            f"Filled levels: {init_filled} → {final_filled} из {final_total or 'unknown'}."
        )
        lines.append(
            f"Spent: {init_spent:.2f}$ → {final_spent:.2f}$."
        )
        if final_avg is not None:
            try:
                avg_val = float(final_avg)
                lines.append(f"Avg price: {avg_val:.8f}")
            except Exception:
                lines.append(f"Avg price: {final_avg}")
        else:
            lines.append("Avg price: --")
        lines.append(f"Campaign status: {status}")

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
        # Режим торговли (SIM/LIVE) на момент старта кампании
        mode = get_trade_mode()
        if is_sim_mode():
            # TODO (Шаг 2): симуляция исполнения DCA-сетки без реальных ордеров Binance.
            pass
        elif is_live_mode():
            # TODO (Шаг 3–4): боевой режим, постановка реальных ордеров Binance по сетке.
            pass


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
            await message.answer("Использование: /dca status <symbol|active|all>")
            return

        arg = parts[2].upper()

        # /dca status active — все активные кампании
        if arg == "ACTIVE":
            from pathlib import Path as _Path

            storage = STORAGE_PATH
            count = 0
            for path in sorted(storage.glob("*_grid.json")):
                try:
                    raw = path.read_text(encoding="utf-8")
                    grid = json.loads(raw)
                except Exception:
                    continue
                if grid.get("campaign_end_ts"):
                    # уже завершена — пропускаем
                    continue

                symbol = str(grid.get("symbol") or path.name.replace("_grid.json", "")).upper()
                text_block = build_dca_status_text(symbol, storage_dir=STORAGE_DIR)
                await message.answer(f"<pre>{text_block}</pre>", parse_mode="HTML")
                count += 1

            if count == 0:
                await message.answer("DCA: активных кампаний не найдено.")
            return

        # /dca status all — все кампании, для которых есть сетка
        if arg == "ALL":
            from pathlib import Path as _Path

            storage = STORAGE_PATH
            count = 0
            for path in sorted(storage.glob("*_grid.json")):
                try:
                    raw = path.read_text(encoding="utf-8")
                    grid = json.loads(raw)
                except Exception:
                    continue

                symbol = str(grid.get("symbol") or path.name.replace("_grid.json", "")).upper()
                text_block = build_dca_status_text(symbol, storage_dir=STORAGE_DIR)
                await message.answer(f"<pre>{text_block}</pre>", parse_mode="HTML")
                count += 1

            if count == 0:
                await message.answer("DCA: кампаний (сеток) не найдено.")
            return

        # /dca status <symbol>
        symbol = arg
        gpath = _grid_path(symbol)
        try:
            raw = gpath.read_text(encoding="utf-8")
            grid = json.loads(raw)
        except Exception:
            await message.answer(f"DCA: сетка для {symbol} не найдена.")
            return

        msg_lines = _build_status_lines_from_grid(grid)
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
        grid["closed_reason"] = "manual"

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

        # Сразу показываем финальный статус кампании в том же чате
        text_block = build_dca_status_text(symbol, storage_dir=STORAGE_DIR)
        await message.answer(f"<pre>{text_block}</pre>", parse_mode="HTML")
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

        # Запрещаем менять конфиг, если есть активная кампания
        if _has_active_campaign(symbol):
            await message.answer(f"DCA: для {symbol} есть активная кампания. Сначала /dca stop {symbol}, затем меняйте конфиг.")
            return

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
            await message.answer("Неизвестное поле, ожидается budget или levels.")
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

        # Запрещаем менять enabled, если есть активная кампания
        if _has_active_campaign(symbol):
            await message.answer(f"DCA: для {symbol} есть активная кампания. Сначала /dca stop {symbol}, затем меняйте конфиг.")
            return

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