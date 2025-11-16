import os
from pathlib import Path
from typing import List

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.types import FSInputFile

# Хранилище файлов
STORAGE_DIR = os.environ.get("STORAGE_DIR", "storage")
STORAGE_PATH = Path(STORAGE_DIR)

router = Router()


def list_storage_files() -> List[Path]:
    """Вернуть отсортированный список файлов в STORAGE_DIR."""
    if not STORAGE_PATH.exists():
        return []
    files = [p for p in STORAGE_PATH.iterdir() if p.is_file()]
    files.sort(key=lambda p: p.name.lower())
    return files


@router.message(Command("data"), ~F.document)
async def cmd_data(message: types.Message) -> None:
    """
    Управление файлами в STORAGE_DIR.

    /data                  — список файлов
    /data export all       — отправить все файлы
    /data export <names>   — отправить указанные файлы (через запятую)
    /data delete all       — удалить все файлы
    /data delete <names>   — удалить указанные файлы (через запятую)
    """
    text = (message.text or "").strip()
    if not text:
        await message.answer("Команда /data.")
        return

    parts = text.split(maxsplit=2)
    if not parts:
        await message.answer("Команда /data.")
        return

    # Без аргументов: показать список файлов
    if len(parts) == 1:
        files = list_storage_files()
        if not files:
            await message.answer("В STORAGE_DIR пока нет файлов.")
            return

        lines = []
        for p in files:
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            lines.append(f"{p.name} ({size} байт)")

        await message.answer("Файлы в STORAGE_DIR:\n" + "\n".join(lines))
        return

    subcmd = parts[1].lower()
    args = parts[2] if len(parts) > 2 else ""

    # /data export ...
    if subcmd == "export":
        arg = args.strip()
        if not arg:
            await message.answer(
                "Укажите имя файла или all.\n"
                "Примеры:\n"
                "/data export dca_config.json\n"
                "/data export all"
            )
            return

        # /data export all
        if arg.lower() == "all":
            files = list_storage_files()
            if not files:
                await message.answer("В STORAGE_DIR нет файлов для отправки.")
                return

            sent = 0
            for path in files:
                try:
                    doc = FSInputFile(path)
                    await message.answer_document(doc)
                    sent += 1
                except Exception:
                    # Пропускаем проблемные файлы
                    continue

            if sent == 0:
                await message.answer("Не удалось отправить файлы из STORAGE_DIR.")
            return

        # /data export name1,name2
        names = [n.strip() for n in arg.split(",") if n.strip()]
        if not names:
            await message.answer("Не найдено имён файлов для экспорта.")
            return

        for name in names:
            if "/" in name or "\\" in name:
                await message.answer(f"Некорректное имя файла: {name}")
                continue
            path = STORAGE_PATH / name
            if not path.exists() or not path.is_file():
                await message.answer(f"Файл {name} не найден в STORAGE_DIR.")
                continue
            try:
                doc = FSInputFile(path)
                await message.answer_document(doc)
            except Exception:
                await message.answer(f"Не удалось отправить файл {name}.")
        return

    # /data delete ...
    if subcmd == "delete":
        arg = args.strip()
        if not arg:
            await message.answer(
                "Укажите имя файла или all.\n"
                "Примеры:\n"
                "/data delete dca_config.json\n"
                "/data delete all"
            )
            return

        # /data delete all
        if arg.lower() == "all":
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

        # /data delete name1,name2
        names = [n.strip() for n in arg.split(",") if n.strip()]
        if not names:
            await message.answer("Не найдено имён файлов для удаления.")
            return

        deleted = 0
        for name in names:
            if "/" in name or "\\" in name:
                await message.answer(f"Некорректное имя файла: {name}")
                continue
            path = STORAGE_PATH / name
            if not path.exists() or not path.is_file():
                await message.answer(f"Файл {name} не найден в STORAGE_DIR.")
                continue
            try:
                path.unlink()
                deleted += 1
            except Exception:
                await message.answer(f"Не удалось удалить файл {name}.")

        await message.answer(f"Удалено файлов: {deleted}.")
        return

    # Неизвестная подкоманда
    await message.answer(
        "Неизвестная подкоманда для /data.\n"
        "Доступно: export, delete, либо просто /data для списка файлов."
    )


@router.message(F.document)
async def handle_any_document(message: types.Message) -> None:
    """Автоматически сохраняем любой документ в STORAGE_DIR."""
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    filename = message.document.file_name or "file.bin"

    # Простейшая защита от путей вида ../../etc/passwd
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
