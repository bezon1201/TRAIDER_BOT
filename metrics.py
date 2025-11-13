import os
import json
import time
from pathlib import Path
from typing import List, Optional

import aiohttp
from aiogram import Router, types
from aiogram.filters import Command

router = Router()

# Путь к persistent-диску Render
STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)
SYMBOLS_FILE = STORAGE_PATH / "symbols_list.json"

# Путь к Bot_commands.txt (лежит в корне проекта)
PROJECT_ROOT = Path(__file__).resolve().parent
BOT_COMMANDS_FILE = PROJECT_ROOT / "Bot_commands.txt"

TF1 = os.environ.get("TF1", "12")
TF2 = os.environ.get("TF2", "6")


def load_symbols() -> List[str]:
    """Загрузить список символов из symbols_list.json."""
    try:
        if not SYMBOLS_FILE.exists():
            return []
        with SYMBOLS_FILE.open("r", encoding="utf-8") as f:
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
    """Сохранить список символов в symbols_list.json."""
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    data = {"symbols": symbols}
    with SYMBOLS_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _symbol_raw_path(symbol: str) -> Path:
    """Путь к файлу сырья <SYMBOL>.json."""
    return STORAGE_PATH / f"{symbol}.json"


def tf_to_interval(tf: str) -> str:
    """Преобразовать TF (например, '12' или '12h') в формат интервала Binance."""
    tf_norm = (tf or "").strip().lower()
    if not tf_norm:
        return "1h"
    # если уже содержит буквы (например, 12h, 4h, 1d) — отдаем как есть
    if any(ch.isalpha() for ch in tf_norm):
        return tf_norm
    # иначе считаем, что это количество часов
    return f"{tf_norm}h"


async def fetch_binance_klines(symbol: str, interval: str, limit: int = 100) -> Optional[list]:
    """Получить свечи с Binance Spot public API.

    Возвращает список словарей с полями ts, o, h, l, c, v.
    При ошибке возвращает None.
    """
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except Exception:
        return None

    candles = []
    for item in data:
        try:
            open_time = int(item[0])
            o = float(item[1])
            h = float(item[2])
            l = float(item[3])
            c = float(item[4])
            v = float(item[5])
            candles.append(
                {
                    "ts": open_time // 1000,
                    "o": o,
                    "h": h,
                    "l": l,
                    "c": c,
                    "v": v,
                }
            )
        except Exception:
            continue

    # Обрезаем до не более 100 на всякий случай
    return candles[-limit:]


async def update_symbol_raw(symbol: str) -> None:
    """Создать или обновить сырьевой файл <SYMBOL>.json.

    - Если файла нет: создаём каркас без запросов к Binance.
    - Если файл есть: подтягиваем ~100 свечей по TF1 и TF2 с Binance и подрезаем.
    """
    path = _symbol_raw_path(symbol)
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    now_ts = int(time.time())
    existed = path.exists()

    if existed:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    else:
        data = {}

    data["symbol"] = symbol
    data["tf1"] = TF1
    data["tf2"] = TF2
    data["updated_ts"] = now_ts

    raw = data.get("raw")
    if not isinstance(raw, dict):
        raw = {}
    data["raw"] = raw

    # гарантируем наличие блоков по TF1/TF2
    if TF1 not in raw or not isinstance(raw.get(TF1), dict):
        raw[TF1] = {}
    if TF2 not in raw or not isinstance(raw.get(TF2), dict):
        raw[TF2] = {}

    # очищаем старые ключи метрик, если они были
    for tf_key in (TF1, TF2):
        block = raw.get(tf_key)
        if isinstance(block, dict):
            block.pop("ma30", None)
            block.pop("ma90", None)
            block.pop("atr14", None)

    if existed:
        # тянем свечи только если файл уже был создан ранее
        interval1 = tf_to_interval(TF1)
        interval2 = tf_to_interval(TF2)

        candles1 = await fetch_binance_klines(symbol, interval1, limit=100)
        candles2 = await fetch_binance_klines(symbol, interval2, limit=100)

        if candles1:
            raw[TF1]["candles"] = candles1[-100:]
        else:
            # если не удалось получить, хотя бы гарантируем поле
            raw[TF1].setdefault("candles", [])

        if candles2:
            raw[TF2]["candles"] = candles2[-100:]
        else:
            raw[TF2].setdefault("candles", [])
    else:
        # новый файл: только каркас, без реальных данных
        raw[TF1].setdefault("candles", [])
        raw[TF2].setdefault("candles", [])

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@router.message(Command("symbols"))
async def cmd_symbols(message: types.Message):
    """Управление списком торговых пар.

    /symbols                - показать текущий список
    /symbols btcusdc,...    - задать список заново
    """
    text = message.text or ""
    parts = text.split(maxsplit=1)

    # Вариант без аргументов: просто показать список
    if len(parts) == 1:
        symbols = load_symbols()
        if not symbols:
            await message.answer("Список торговых пар пуст.")
        else:
            body = "\n".join(symbols)
            await message.answer(f"Текущий список торговых пар:\n{body}")
        return

    # Вариант с аргументами: перезаписать список
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

    save_symbols(symbols)
    body = "\n".join(symbols)
    await message.answer(f"Список торговых пар обновлён:\n{body}")


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    """Отправить содержимое Bot_commands.txt пользователю."""
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


@router.message(Command("now"))
async def cmd_now(message: types.Message):
    """Создать или обновить сырые файлы <SYMBOL>.json.

    /now            - для всех пар из symbols_list.json
    /now btcusdc    - только для указанных пар
    """
    text = message.text or ""
    parts = text.split(maxsplit=1)

    # Без аргументов: работаем по всему списку symbols_list.json
    if len(parts) == 1:
        symbols = load_symbols()
        if not symbols:
            await message.answer("В symbols_list нет ни одной пары.")
            return

        for symbol in symbols:
            await update_symbol_raw(symbol)

        await message.answer(f"Обновили {len(symbols)} пар.")
        return

    # С аргументами: только указанные пары
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

    for symbol in symbols:
        await update_symbol_raw(symbol)

    if len(symbols) == 1:
        await message.answer(f"Обновили {symbols[0]}.")
    else:
        await message.answer(f"Обновили {len(symbols)} пар.")
