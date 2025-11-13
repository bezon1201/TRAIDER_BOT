import os
import json
import time
from pathlib import Path

import aiohttp
from aiogram import Router, types
from aiogram.filters import Command

router = Router()

# Путь к persistent-диску Render
STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
SYMBOLS_FILE = Path(STORAGE_DIR) / "symbols_list.json"

# Путь к Bot_commands.txt (лежит в корне проекта)
PROJECT_ROOT = Path(__file__).resolve().parent
BOT_COMMANDS_FILE = PROJECT_ROOT / "Bot_commands.txt"

TF1 = os.environ.get("TF1", "12")
TF2 = os.environ.get("TF2", "6")


def load_symbols() -> list[str]:
    """Загрузить список символов из symbols_list.json."""
    try:
        if not SYMBOLS_FILE.exists():
            return []
        with SYMBOLS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        symbols = data.get("symbols", [])
        if not isinstance(symbols, list):
            return []
        # нормализуем к строкам upper
        result: list[str] = []
        for s in symbols:
            if not isinstance(s, str):
                continue
            s_up = s.upper()
            if s_up and s_up not in result:
                result.append(s_up)
        return result
    except Exception:
        return []


def save_symbols(symbols: list[str]) -> None:
    """Сохранить список символов в symbols_list.json."""
    SYMBOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"symbols": symbols}
    with SYMBOLS_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _symbol_raw_path(symbol: str) -> Path:
    """Путь к файлу сырья <SYMBOL>.json."""
    return Path(STORAGE_DIR) / f"{symbol}.json"


def _tf_to_interval(tf: str) -> str:
    """Преобразовать TF в интервал Binance (по умолчанию часы)."""
    s = str(tf).strip()
    if not s:
        return "1h"
    # если уже содержит букву интервала, используем как есть (например '4h')
    if s[-1].isalpha():
        return s
    return s + "h"


async def fetch_klines(symbol: str, tf: str, limit: int = 50) -> list[dict]:
    """Запросить свечи с Binance Spot для символа и таймфрейма.

    Возвращает список dict'ов с полями ts, o, h, l, c, v.
    """
    interval = _tf_to_interval(tf)
    url = (
        "https://api.binance.com/api/v3/klines"
        f"?symbol={symbol}&interval={interval}&limit={limit}"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
    except Exception:
        return []

    candles: list[dict] = []
    for item in data:
        try:
            # item: [openTime, open, high, low, close, volume, closeTime, ...]
            open_time_ms = int(item[0])
            o = float(item[1])
            h = float(item[2])
            l = float(item[3])
            c = float(item[4])
            v = float(item[5])
        except (ValueError, TypeError, IndexError):
            continue

        candles.append(
            {
                "ts": open_time_ms // 1000,
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v,
            }
        )

    return candles


async def update_symbol_raw(symbol: str) -> None:
    """Создать или обновить сырьевой файл <SYMBOL>.json.

    Если файл существует — подтягиваем свечи с Binance и перезаписываем 'candles'
    для TF1 и TF2. Если файла нет — создаём только каркас без запросов к Binance.
    """
    path = _symbol_raw_path(symbol)
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)

    if existed:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    else:
        # создаём каркас как раньше
        now_ts = int(time.time())
        data = {
            "symbol": symbol,
            "tf1": TF1,
            "tf2": TF2,
            "updated_ts": now_ts,
            "raw": {
                TF1: {"candles": [], "ma30": [], "ma90": [], "atr14": []},
                TF2: {"candles": [], "ma30": [], "ma90": [], "atr14": []},
            },
        }

    # гарантируем структуру raw
    raw = data.setdefault("raw", {})
    raw.setdefault(TF1, {"candles": [], "ma30": [], "ma90": [], "atr14": []})
    raw.setdefault(TF2, {"candles": [], "ma30": [], "ma90": [], "atr14": []})

    # если файл был, подтягиваем свечи
    if existed:
        candles_tf1 = await fetch_klines(symbol, TF1, limit=50)
        if candles_tf1:
            raw[TF1]["candles"] = candles_tf1

        candles_tf2 = await fetch_klines(symbol, TF2, limit=50)
        if candles_tf2:
            raw[TF2]["candles"] = candles_tf2

        data["symbol"] = data.get("symbol") or symbol
        data["tf1"] = TF1
        data["tf2"] = TF2
        data["updated_ts"] = int(time.time())

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
    symbols: list[str] = []
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

    Если файл уже существует — подтягиваются последние свечи с Binance (50 шт)
    по TF1 и TF2 и перезаписывается блок raw[TF].candles.
    Если файла нет — создаётся только каркас структуры, без запросов к Binance.
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
    symbols: list[str] = []
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
