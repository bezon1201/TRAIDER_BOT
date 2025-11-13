import os
from pathlib import Path
from typing import List

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import FSInputFile

router = Router()

# Путь к persistent-диску Render
STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)


def list_storage_files() -> List[Path]:
    """Список файлов в STORAGE_DIR (только файлы, без подпапок)."""
    if not STORAGE_PATH.exists():
        return []
    return sorted(p for p in STORAGE_PATH.iterdir() if p.is_file())


def safe_names_from_args(args: str) -> list[str]:
    """Разобрать имена файлов из строки аргументов.

    Убираем пробелы, пустые элементы и запрещаем пути с разделителями.
    """
    result: list[str] = []
    for item in args.split(","):
        name = item.strip()
        if not name:
            continue
        # не позволяем указывать подкаталоги
        if "/" in name or chr(92) in name:
            continue
        if name not in result:
            result.append(name)
    return result


@router.message(Command("data"))
async def cmd_data(message: types.Message):
    """Работа с файлами в STORAGE_DIR.

    /data
        показать список файлов

    /data export <files>
    /data export all

    /data delete <files>
    /data delete all

    Для импорта используется отправка файла с подписью `/data import`.
    """
    text = message.text or ""
    parts = text.split(maxsplit=2)

    # Если это просто "/data" без подкоманд
    if len(parts) == 1:
        files = list_storage_files()
        if not files:
            await message.answer("В STORAGE_DIR нет файлов.")
            return
        names = ", ".join(p.name for p in files)
        await message.answer(names)
        return

    # Подкоманда: export / delete / import (текстовая форма)
    subcmd = parts[1].lower()

    # IMPORT как текстовой командой обрабатывать не будем —
    # для импорта нужен файл с caption "/data import".
    if subcmd == "import":
        await message.answer("Для импорта отправьте файл с подписью `/data import`.")
        return

    # Нужны аргументы для export/delete, кроме варианта 'all'
    if len(parts) == 2:
        await message.answer("Не указаны аргументы для /data.")
        return

    args = parts[2]

    # EXPORT
    if subcmd == "export":
        if args.strip().lower() == "all":
            files = list_storage_files()
            if not files:
                await message.answer("В STORAGE_DIR нет файлов для экспорта.")
                return
            for path in files:
                doc = FSInputFile(path)
                await message.answer_document(doc)
            return

        names = safe_names_from_args(args)
        if not names:
            await message.answer("Не удалось распознать ни одного файла.")
            return

        sent_any = False
        missing: list[str] = []
        for name in names:
            path = STORAGE_PATH / name
            if not path.is_file():
                missing.append(name)
                continue
            doc = FSInputFile(path)
            await message.answer_document(doc)
            sent_any = True

        if missing and sent_any:
            await message.answer("Не найдены: " + ", ".join(missing))
        elif missing and not sent_any:
            await message.answer("Не найдены указанные файлы.")
        return

    # DELETE
    if subcmd == "delete":
        if args.strip().lower() == "all":
            files = list_storage_files()
            if not files:
                await message.answer("В STORAGE_DIR нет файлов для удаления.")
                return
            for path in files:
                try:
                    path.unlink()
                except Exception:
                    pass
            await message.answer("Все файлы в STORAGE_DIR удалены.")
            return

        names = safe_names_from_args(args)
        if not names:
            await message.answer("Не удалось распознать ни одного файла.")
            return

        deleted = 0
        for name in names:
            path = STORAGE_PATH / name
            if path.is_file():
                try:
                    path.unlink()
                    deleted += 1
                except Exception:
                    pass

        if deleted == 0:
            await message.answer("Не удалось удалить указанные файлы.")
        elif deleted == 1:
            await message.answer("Удалён 1 файл.")
        else:
            await message.answer(f"Удалено файлов: {deleted}.")
        return

    # Неизвестная подкоманда
    await message.answer("Неизвестная подкоманда для /data.")


@router.message()
async def handle_data_import(message: types.Message):
    """Импорт файла в STORAGE_DIR по подписи `/data import` в caption.

    Ожидаем, что сообщение содержит документ и caption начинается с '/data import'.
    """
    if not message.document:
        return

    caption = message.caption or ""
    if not caption:
        return

    # Проверяем, что caption начинается с "/data import"
    parts = caption.split(maxsplit=2)
    first = parts[0] if parts else ""
    if first not in {"/data", "/data@" + (message.bot.username or "")}:
        return
    if len(parts) < 2 or parts[1].lower() != "import":
        return

    # Здесь у нас есть документ и корректная команда импорта
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    filename = message.document.file_name or "file.bin"
    # запрещаем поддиректории в имени
    if "/" in filename or chr(92) in filename:
        await message.answer("Некорректное имя файла.")
        return

    dest_path = STORAGE_PATH / filename

    try:
        file = await message.bot.get_file(message.document.file_id)
        await message.bot.download_file(file.file_path, destination=dest_path)
        await message.answer(f"Файл {filename} импортирован в STORAGE_DIR.")
    except Exception:
        await message.answer("Не удалось импортировать файл.")
