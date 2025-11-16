
import os
from pathlib import Path
from typing import List

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile

router = Router()

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)


def list_storage_files() -> List[Path]:
    """
    Вернуть список файлов в STORAGE_DIR (только файлы первого уровня).
    """
    if not STORAGE_PATH.exists():
        return []
    items: List[Path] = []
    try:
        for p in STORAGE_PATH.iterdir():
            if p.is_file():
                items.append(p)
    except Exception:
        return []
    return sorted(items, key=lambda p: p.name.lower())


@router.message(Command("data"))
async def cmd_data(message: types.Message) -> None:
    """
    Управление файлами в STORAGE_DIR.

    /data                      — показать список файлов.
    /data export <name>        — отправить файл из STORAGE_DIR.
    /data delete <name>        — удалить файл из STORAGE_DIR.
    """
    text = message.text or ""
    parts = text.split()
    if not parts:
        await message.answer("Не указаны аргументы для /data.")
        return

    # parts[0] == /data или /data@bot
    if len(parts) == 1:
        # просто список файлов
        files = list_storage_files()
        if not files:
            await message.answer("В STORAGE_DIR пока нет файлов.")
            return
        lines = ["Файлы в STORAGE_DIR:"]
        for p in files:
            try:
                size = p.stat().st_size
            except Exception:
                size = 0
            lines.append(f"- {p.name} ({size} байт)")
        await message.answer("\n".join(lines))
        return

    subcmd = parts[1].lower()

    # Для export/delete нам нужны аргументы
    if subcmd in {"export", "delete"} and len(parts) < 3:
        await message.answer("Не указаны аргументы для /data.")
        return

    if subcmd == "export":
        name = parts[2]
        path = STORAGE_PATH / name
        if not path.exists() or not path.is_file():
            await message.answer(f"Файл {name} не найден в STORAGE_DIR.")
            return
        try:
            doc = FSInputFile(path)
            await message.answer_document(doc)
        except Exception:
            await message.answer("Не удалось отправить файл.")
        return

    if subcmd == "delete":
        name = parts[2]
        path = STORAGE_PATH / name
        if not path.exists() or not path.is_file():
            await message.answer(f"Файл {name} не найден в STORAGE_DIR.")
            return
        try:
            path.unlink()
            await message.answer(f"Файл {name} удалён из STORAGE_DIR.")
        except Exception:
            await message.answer("Не удалось удалить файл.")
        return

    await message.answer("Неизвестная подкоманда для /data.")
    return


@router.message(F.document)
async def handle_any_document(message: types.Message) -> None:
    """
    Автоимпорт: любой отправленный в чат файл-документ сохраняется в STORAGE_DIR.
    Команда /data import больше не нужна.

    Если файл с таким именем уже существует, он будет перезаписан.
    """
    if not message.document:
        return

    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    filename = message.document.file_name or "file.bin"

    # Защита от обхода путей
    if "/" in filename or "\\" in filename:
        await message.answer("Некорректное имя файла.")
        return

    dest_path = STORAGE_PATH / filename

    try:
        file = await message.bot.get_file(message.document.file_id)
        await message.bot.download_file(file.file_path, destination=dest_path)
        await message.answer(f"Файл {filename} сохранён в STORAGE_DIR.")
    except Exception:
        await message.answer("Не удалось сохранить файл.")
