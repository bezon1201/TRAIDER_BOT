
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
    if not STORAGE_PATH.exists():
        return []
    if not STORAGE_PATH.is_dir():
        return []
    files: List[Path] = []
    for item in STORAGE_PATH.iterdir():
        if item.is_file():
            files.append(item)
    files.sort(key=lambda p: p.name.lower())
    return files


def safe_names_from_args(args: str) -> list[str]:
    """
    Разобрать список имён файлов из аргументов команды, отфильтровав
    заведомо некорректные варианты (с / или \\).
    """
    result: list[str] = []
    for item in args.split(","):
        name = item.strip()
        if not name:
            continue
        if "/" in name or "\\" in name:
            # Защита от путей
            continue
        if name not in result:
            result.append(name)
    return result


@router.message(Command("data"), ~F.document)
async def cmd_data(message: types.Message):
    """
    Управление файлами в STORAGE_DIR.

    /data                      — показать список файлов.
    /data export all           — отправить все файлы.
    /data export <FILES>       — отправить указанные файлы.
    /data delete all           — удалить все файлы.
    /data delete <FILES>       — удалить указанные файлы.
    """
    text = (message.text or "").strip()
    if not text:
        await message.answer("Не указаны аргументы для /data.")
        return

    parts = text.split(maxsplit=2)
    if not parts:
        await message.answer("Не указаны аргументы для /data.")
        return

    # Только "/data" без аргументов — показать список файлов
    if len(parts) == 1:
        files = list_storage_files()
        if not files:
            await message.answer("В STORAGE_DIR нет файлов.")
            return
        names = ", ".join(p.name for p in files)
        await message.answer(names)
        return

    if len(parts) < 2:
        await message.answer("Не указаны аргументы для /data.")
        return

    subcmd = parts[1].lower()

    # Для export/delete нам обязательно нужны аргументы
    if subcmd in {"export", "delete"} and len(parts) < 3:
        await message.answer("Не указаны аргументы для /data.")
        return

    args = parts[2] if len(parts) >= 3 else ""

    if subcmd == "export":
        # /data export all
        if args.strip().lower() == "all":
            files = list_storage_files()
            if not files:
                await message.answer("В STORAGE_DIR нет файлов для экспорта.")
                return
            for path in files:
                try:
                    doc = FSInputFile(path)
                    await message.answer_document(doc)
                except Exception:
                    # Пропускаем файлы, которые не удалось отправить
                    continue
            return

        # /data export <FILES>
        names = safe_names_from_args(args)
        if not names:
            await message.answer("Не удалось распознать ни одного файла для экспорта.")
            return

        for name in names:
            path = STORAGE_PATH / name
            if not path.is_file():
                await message.answer(f"Файл {name} не найден в STORAGE_DIR.")
                continue
            try:
                doc = FSInputFile(path)
                await message.answer_document(doc)
            except Exception:
                await message.answer(f"Не удалось отправить файл {name}.")
        return

    if subcmd == "delete":
        # /data delete all
        if args.strip().lower() == "all":
            files = list_storage_files()
            if not files:
                await message.answer("В STORAGE_DIR нет файлов для удаления.")
                return
            deleted = 0
            for path in files:
                try:
                    path.unlink()
                    deleted += 1
                except Exception:
                    continue
            await message.answer(f"Удалено файлов: {deleted}.")
            return

        # /data delete <FILES>
        names = safe_names_from_args(args)
        if not names:
            await message.answer("Не удалось распознать ни одного файла для удаления.")
            return

        deleted = 0
        for name in names:
            path = STORAGE_PATH / name
            if not path.is_file():
                continue
            try:
                path.unlink()
                deleted += 1
            except Exception:
                continue

        if deleted == 0:
            await message.answer("Ни один файл не был удалён.")
        else:
            await message.answer(f"Удалено файлов: {deleted}.")
        return

    await message.answer("Неизвестная подкоманда для /data.")


@router.message(F.document)
async def handle_any_document(message: types.Message) -> None:
    """
    Любой файл, отправленный боту как документ, автоматически сохраняется
    в STORAGE_DIR. Если файл с таким именем уже существует — перезаписываем.
    """
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    filename = message.document.file_name or "file.bin"

    # Простая защита от путей
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
