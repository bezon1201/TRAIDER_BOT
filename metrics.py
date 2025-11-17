import os
import json
import time
import math
from pathlib import Path
from typing import List, Dict, Any, Optional

import aiohttp
from aiogram import Router, types, F
from aiogram.filters import Command
from coin_state import (
    MARKET_PUBLISH,
    calc_market_mode_for_symbol,
    recalc_state_for_symbol,
)
from grid_roll import roll_grid_for_symbol
from grid_sim import simulate_bar_for_symbol


router = Router()

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)

PROJECT_ROOT = Path(__file__).resolve().parent
BOT_COMMANDS_FILE = PROJECT_ROOT / "Bot_commands.txt"

TF1 = os.environ.get("TF1", "12")
TF2 = os.environ.get("TF2", "6")


def _symbols_file() -> Path:
    return STORAGE_PATH / "symbols_list.json"


def load_symbols() -> List[str]:
    path = _symbols_file()
    try:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        symbols = data.get("symbols", [])
        if not isinstance(symbols, list):
            return []
        result: List[str] = []
        for s in symbols:
            if not isinstance(s, str):
                continue
            s_up = s.upper()
            if s_up and s_up not in result:
                result.append(s_up)
        return result
    except Exception:
        return []


def save_symbols(symbols: List[str]) -> None:
    path = _symbols_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"symbols": symbols}, f, ensure_ascii=False, indent=2)




def _has_any_active_campaign() -> bool:
    """
    Проверяет, есть ли хотя бы одна активная DCA-кампания
    (любой *_grid.json с campaign_end_ts == None/0 в STORAGE_DIR).
    """
    storage = STORAGE_PATH
    try:
        grid_files = list(storage.glob("*_grid.json"))
    except Exception:
        return False

    for path in grid_files:
        try:
            raw = path.read_text(encoding="utf-8")
            grid = json.loads(raw)
        except Exception:
            continue
        if not grid.get("campaign_end_ts"):
            return True
    return False


def _symbol_raw_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}.json"


def _raw_market_path(symbol: str) -> Path:
    return STORAGE_PATH / f"{symbol}raw_market.jsonl"


def _load_last_tf1_candle(symbol: str) -> Optional[Dict[str, Any]]:
    """Прочитать последнюю свечу TF1 из STORAGE_DIR/<SYMBOL>.json.

    Возвращает dict свечи в том формате, в каком её пишет update_symbol_raw,
    или None, если данных нет или данные повреждены.
    """
    path = _symbol_raw_path(symbol)
    if not path.exists():
        return None
    try:
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except Exception:
        return None

    tf1 = data.get("tf1") or os.environ.get("TF1") or ""
    raw = data.get("raw") or {}
    tf_block = raw.get(tf1)
    if not isinstance(tf_block, dict):
        return None
    candles = tf_block.get("candles")
    if not isinstance(candles, list) or not candles:
        return None

    last = candles[-1]
    if not isinstance(last, dict):
        return None
    return last

def tf_to_interval(tf: str) -> str:
    # По умолчанию считаем, что tf — число часов.
    # Поддерживаем два варианта:
    # - '12', '6'  -> '12h', '6h'
    # - '12h', '6h', '1m', '1d' -> возвращаем как есть.
    tf = str(tf).strip()

    # Если уже в формате Binance (1m, 5m, 1h, 12h, 1d) — возвращаем как есть
    if tf.endswith("m") or tf.endswith("h") or tf.endswith("d"):
        return tf

    # Если это просто число — считаем, что это часы и добавляем "h"
    if tf.isdigit():
        return f"{tf}h"

    # На всякий случай возвращаем как есть, чтобы не падать
    return tf




def sma(values: List[float], period: int) -> List[float]:
    if period <= 0 or len(values) < period:
        return []
    res: List[float] = []
    window_sum = sum(values[:period])
    res.append(window_sum / period)
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        res.append(window_sum / period)
    return res


def atr14(candles: List[Dict[str, Any]], period: int = 14) -> List[float]:
    if len(candles) <= period:
        return []
    trs: List[float] = []
    prev_close = float(candles[0]["c"])
    for c in candles[1:]:
        high = float(c["h"])
        low = float(c["l"])
        close = float(c["c"])
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        trs.append(tr)
        prev_close = close
    if len(trs) < period:
        return []
    return sma(trs, period)


def make_signal(ma30_arr: List[float], ma90_arr: List[float], atr_arr: List[float]) -> Dict[str, Any]:
    if not ma30_arr or not ma90_arr or not atr_arr:
        return {
            "value": "RANGE",
            "ma30": 0.0,
            "ma90": 0.0,
            "atr14": 0.0,
            "d_now": 0.0,
            "d_prev": 0.0,
        }
    ma30 = float(ma30_arr[-1])
    ma90 = float(ma90_arr[-1])
    atr = float(atr_arr[-1])
    if atr <= 0:
        return {
            "value": "RANGE",
            "ma30": ma30,
            "ma90": ma90,
            "atr14": atr,
            "d_now": 0.0,
            "d_prev": 0.0,
        }
    d_now = ma30 - ma90
    if len(ma30_arr) > 1 and len(ma90_arr) > 1:
        d_prev = float(ma30_arr[-2]) - float(ma90_arr[-2])
    else:
        d_prev = 0.0
    H = 0.4 * atr
    S = 0.1 * atr
    if d_now > H and (d_prev >= 0 or abs(d_prev) <= S):
        val = "UP"
    elif d_now < -H and (d_prev <= 0 or abs(d_prev) <= S):
        val = "DOWN"
    else:
        val = "RANGE"
    return {
        "value": val,
        "ma30": ma30,
        "ma90": ma90,
        "atr14": atr,
        "d_now": d_now,
        "d_prev": d_prev,
    }


async def fetch_json(session: aiohttp.ClientSession, url: str, params: Dict[str, Any]) -> Any:
    async with session.get(url, params=params, timeout=10) as resp:
        resp.raise_for_status()
        return await resp.json()


async def update_symbol_raw(session: aiohttp.ClientSession, symbol: str) -> Dict[str, Any]:
    path = _symbol_raw_path(symbol)
    now_ts = int(time.time())
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    else:
        data = {}

    data.setdefault("symbol", symbol)
    data["tf1"] = TF1
    data["tf2"] = TF2
    data["updated_ts"] = now_ts
    raw = data.setdefault("raw", {})
    block1 = raw.setdefault(TF1, {})
    block2 = raw.setdefault(TF2, {})

    base_url = "https://api.binance.com"

    # --- свечи TF1 ---
    candles1: list = []
    try:
        interval1 = tf_to_interval(TF1)
        kl1 = await fetch_json(
            session,
            f"{base_url}/api/v3/klines",
            {"symbol": symbol, "interval": interval1, "limit": 100},
        )
        for k in kl1:
            candles1.append(
                {
                    "ts": int(k[0] / 1000),
                    "o": float(k[1]),
                    "h": float(k[2]),
                    "l": float(k[3]),
                    "c": float(k[4]),
                    "v": float(k[5]),
                }
            )
    except Exception:
        candles1 = []

    # --- свечи TF2 ---
    candles2: list = []
    try:
        interval2 = tf_to_interval(TF2)
        kl2 = await fetch_json(
            session,
            f"{base_url}/api/v3/klines",
            {"symbol": symbol, "interval": interval2, "limit": 100},
        )
        for k in kl2:
            candles2.append(
                {
                    "ts": int(k[0] / 1000),
                    "o": float(k[1]),
                    "h": float(k[2]),
                    "l": float(k[3]),
                    "c": float(k[4]),
                    "v": float(k[5]),
                }
            )
    except Exception:
        candles2 = []

    # ограничиваем до 100
    if len(candles1) > 100:
        candles1 = candles1[-100:]
    if len(candles2) > 100:
        candles2 = candles2[-100:]

    block1["candles"] = candles1
    block2["candles"] = candles2

    # --- метрики для рынка по TF1 / TF2 ---
    closes1 = [c["c"] for c in candles1]
    closes2 = [c["c"] for c in candles2]

    ma30_1 = sma(closes1, 30)
    ma90_1 = sma(closes1, 90)
    atr1 = atr14(candles1, 14)

    ma30_2 = sma(closes2, 30)
    ma90_2 = sma(closes2, 90)
    atr2 = atr14(candles2, 14)

    block1["ma30_arr"] = ma30_1
    block1["ma90_arr"] = ma90_1
    block1["atr14_arr"] = atr1
    sig1 = make_signal(ma30_1, ma90_1, atr1)
    block1["signal"] = sig1

    block2["ma30_arr"] = ma30_2
    block2["ma90_arr"] = ma90_2
    block2["atr14_arr"] = atr2
    sig2 = make_signal(ma30_2, ma90_2, atr2)
    block2["signal"] = sig2

    # --- агрегированный режим рынка ---
    # если оба UP -> UP; если любой DOWN -> DOWN; иначе RANGE
    if sig1["value"] == "UP" and sig2["value"] == "UP":
        overall = "UP"
    elif sig1["value"] == "DOWN" or sig2["value"] == "DOWN":
        overall = "DOWN"
    else:
        overall = "RANGE"
    data["market_mode"] = overall

    # --- торговые параметры ---
    trading_params: Dict[str, Any] = {}
    # цены
    try:
        ticker_price = await fetch_json(
            session,
            f"{base_url}/api/v3/ticker/price",
            {"symbol": symbol},
        )
        last_price = float(ticker_price.get("price", 0.0))
    except Exception:
        last_price = 0.0

    bid = last_price
    ask = last_price
    try:
        book = await fetch_json(
            session,
            f"{base_url}/api/v3/ticker/bookTicker",
            {"symbol": symbol},
        )
        bid = float(book.get("bidPrice", bid))
        ask = float(book.get("askPrice", ask))
    except Exception:
        pass

    trading_params["price"] = {
        "last": last_price,
        "bid": bid,
        "ask": ask,
    }

    # exchangeInfo
    symbol_info_block: Dict[str, Any] = {}
    filters_block: Dict[str, Any] = {}
    try:
        info = await fetch_json(
            session,
            f"{base_url}/api/v3/exchangeInfo",
            {"symbol": symbol},
        )
        symbols = info.get("symbols") or []
        if symbols:
            s0 = symbols[0]
            symbol_info_block["base_asset"] = s0.get("baseAsset")
            symbol_info_block["quote_asset"] = s0.get("quoteAsset")
            for f in s0.get("filters", []):
                ftype = f.get("filterType")
                if not ftype:
                    continue
                filters_block[ftype] = f
            lot = filters_block.get("LOT_SIZE") or {}
            price_f = filters_block.get("PRICE_FILTER") or {}
            min_not = filters_block.get("NOTIONAL") or {}
            def _to_float(v):
                try:
                    return float(v)
                except Exception:
                    return 0.0
            symbol_info_block["tick_size"] = _to_float(price_f.get("tickSize", 0))
            symbol_info_block["step_size"] = _to_float(lot.get("stepSize", 0))
            symbol_info_block["min_qty"] = _to_float(lot.get("minQty", 0))
            symbol_info_block["min_notional"] = _to_float(min_not.get("minNotional", 0))
    except Exception:
        pass

    trading_params["symbol_info"] = symbol_info_block
    trading_params["filters"] = filters_block

    # комиссии — пока статично, можно будет потом сделать динамически
    trading_params["fees"] = {
        "maker": 0.0002,
        "taker": 0.0004,
    }

    data["trading_params"] = trading_params

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "overall_mode": overall,
        "signal_tf1": sig1.get("value"),
        "signal_tf2": sig2.get("value"),
        "updated_ts": now_ts,
    }


def append_raw_market_line(symbol: str, info: Dict[str, Any]) -> None:
    path = _raw_market_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": info.get("updated_ts", int(time.time())),
        "symbol": symbol,
        "market_mode": info.get("overall_mode", "RANGE"),
        "tf1": TF1,
        "tf2": TF2,
        "signal_tf1": info.get("signal_tf1"),
        "signal_tf2": info.get("signal_tf2"),
    }
    line = json.dumps(record, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


@router.message(Command("symbols"))
async def cmd_symbols(message: types.Message):
    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) == 1:
        symbols = load_symbols()
        if not symbols:
            await message.answer("Список торговых пар пуст.")
        else:
            body = "\n".join(symbols)
            await message.answer(f"Текущий список торговых пар:\n{body}")
        return

    args = parts[1]

    # Запрещаем изменять список монет, если есть активные DCA-кампании
    if _has_any_active_campaign():
        await message.answer(
            "Symbols: есть активные DCA-кампании. "
            "Сначала остановите их через /dca stop <symbol> для всех активных, затем меняйте список через /symbols ..."
        )
        return

    raw_items = args.split(",")
    symbols: List[str] = []
    for item in raw_items:
        s = item.strip()
        if not s:
            continue
        s_up = s.upper()
        if s_up not in symbols:
            symbols.append(s_up)

    if not symbols:
        await message.answer("Не удалось распознать ни одной торговой пары.")
        return

    save_symbols(symbols)
    body = "\n".join(symbols)
    await message.answer(f"Список торговых пар обновлён:\n{body}")



@router.message(
    F.reply_to_message,
    F.reply_to_message.from_user.is_bot,
    F.reply_to_message.text.contains("Текущий список будет полностью заменён."),
    F.text.regexp(r"^(?!/)")
)
async def handle_symbols_reply_from_menu(message: types.Message):
    """
    Обработка ответа на запрос списка торговых пар из меню
    (кнопка MENU → PAIR → COINS).
    Ожидаем, что пользователь ответит на специальное сообщение-приглашение.
    """
    # Обрабатываем только ответы на наш специальный промпт от бота.
    if not message.reply_to_message:
        return

    reply_msg = message.reply_to_message
    reply_text = reply_msg.text or ""

    # Должен быть ответ именно боту (а не другому пользователю).
    if not getattr(reply_msg.from_user, "is_bot", False):
        return

    # Узкий маркер, чтобы не ловить лишние реплаи.
    if "Текущий список будет полностью заменён." not in reply_text:
        return

    # Если пользователь всё-таки прислал команду (начинается с "/"),
    # отдаём её обычным командным хэндлерам и не трогаем здесь.
    args = (message.text or "").strip()
    if not args:
        await message.answer("Не удалось распознать ни одной торговой пары.")
        return

    if args.startswith("/"):
        # Пусть это обработают другие хэндлеры Command(...)
        return

    # Повторяем ту же защиту, что и в /symbols:
    # запрещаем менять список монет при активных DCA-кампаниях.
    if _has_any_active_campaign():
        await message.answer(
            "Symbols: есть активные DCA-кампании. "
            "Сначала остановите их через /dca stop <symbol> для всех активных, затем меняйте список через /symbols ..."
        )
        return

    raw_items = args.split(",")
    symbols: List[str] = []
    for item in raw_items:
        s = item.strip()
        if not s:
            continue
        s_up = s.upper()
        if s_up not in symbols:
            symbols.append(s_up)

    if not symbols:
        await message.answer("Не удалось распознать ни одной торговой пары.")
        return

    save_symbols(symbols)
    body = "\n".join(symbols)
    await message.answer(f"Список торговых пар обновлён:\n{body}")
@router.message(Command("help"))
async def cmd_help(message: types.Message):
    try:
        with BOT_COMMANDS_FILE.open("r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            await message.answer("Файл Bot_commands.txt пуст.")
            return
        await message.answer(content)
    except FileNotFoundError:
        await message.answer("Файл Bot_commands.txt не найден в корне проекта.")
    except Exception:
        await message.answer("Не удалось прочитать Bot_commands.txt.")




@router.message(Command("market"))
async def cmd_market(message: types.Message):
    """
    Команда /market и /market force.

    /market
        — посчитать режим рынка (UP/DOWN/RANGE) за последние MARKET_PUBLISH часов
          для всех пар из symbols_list.json, без записи в state.

    /market force
        — пересчитать и сохранить SYMBOLstate.json для всех пар, используя уже собранное сырьё.

    /market force <symbol>
        — то же самое, но только для одной конкретной пары.
    """
    text = message.text or ""
    parts = text.split(maxsplit=2)

    # Просто /market — показываем режимы рынка для всех пар
    if len(parts) == 1:
        symbols = load_symbols()
        if not symbols:
            await message.answer("В symbols_list нет ни одной пары.")
            return

        now_ts = int(time.time())
        lines = []
        for sym in symbols:
            mode = calc_market_mode_for_symbol(sym, now_ts=now_ts)
            lines.append(f"{sym}: {mode}")

        body = "\n".join(lines) if lines else "нет данных"
        await message.answer(
            f"Режим рынка за последние {MARKET_PUBLISH} ч для {len(symbols)} пар:\n{body}"
        )
        return

    # Есть подкоманда
    sub = (parts[1] or "").lower()
    if sub != "force":
        await message.answer("Неизвестная подкоманда для /market. Доступно: /market, /market force, /market force <symbol>.")
        return

    # /market force или /market force <symbol>
    if len(parts) == 2:
        symbols = load_symbols()
        if not symbols:
            await message.answer("В symbols_list нет ни одной пары.")
            return
    else:
        raw_items = (parts[2] or "").split(",")
        symbols = []
        for item in raw_items:
            s = item.strip()
            if not s:
                continue
            s_up = s.upper()
            if s_up not in symbols:
                symbols.append(s_up)
        if not symbols:
            await message.answer("Не удалось распознать ни одной торговой пары.")
            return

    now_ts = int(time.time())
    lines = []
    updated = 0
    for sym in symbols:
        state = recalc_state_for_symbol(sym, now_ts=now_ts)
        try:
            roll_grid_for_symbol(sym)
        except Exception:
            pass
        mode = str(state.get("market_mode", "RANGE")).upper()
        lines.append(f"{sym}: {mode}")
        updated += 1

    body = "\n".join(lines) if lines else "нет данных"
    await message.answer(
        f"Обновили state для {updated} пар:\n{body}"
    )


@router.message(Command("now"))
async def cmd_now(message: types.Message):
    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) == 1:
        symbols = load_symbols()
        if not symbols:
            await message.answer("В symbols_list нет ни одной пары.")
            return
    else:
        args = parts[1]
        raw_items = args.split(",")
        symbols: List[str] = []
        for item in raw_items:
            s = item.strip()
            if not s:
                continue
            s_up = s.upper()
            if s_up not in symbols:
                symbols.append(s_up)
        if not symbols:
            await message.answer("Не удалось распознать ни одной торговой пары.")
            return

    async with aiohttp.ClientSession() as session:
        updated = 0
        for sym in symbols:
            info = await update_symbol_raw(session, sym)
            append_raw_market_line(sym, info)
            updated += 1

            # Онлайн-симуляция исполнения DCA-сетки по последней свече TF1 (только SIM)
            try:
                bar = _load_last_tf1_candle(sym)
                if bar is not None:
                    simulate_bar_for_symbol(sym, bar)
            except Exception:
                # Не блокируем /now из-за ошибок симуляции
                pass

    if len(symbols) == 1:
        await message.answer(f"Обновили {symbols[0]}.")
    else:
        await message.answer(f"Обновили {len(symbols)} пар.")

