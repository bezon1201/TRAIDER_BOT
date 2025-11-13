import os
from pathlib import Path
from typing import List

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import FSInputFile

router = Router()

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)


def list_storage_files() -> List[Path]:
    if not STORAGE_PATH.exists():
        return []
    return sorted(p for p in STORAGE_PATH.iterdir() if p.is_file())


def safe_names_from_args(args: str) -> list[str]:
    result: list[str] = []
    for item in args.split(","):
        name = item.strip()
        if not name:
            continue
        if "/" in name or chr(92) in name:
            continue
        if name not in result:
            result.append(name)
    return result


@router.message(Command("data"))
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

    # Просто /data — показываем список файлов
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
                pass

        if deleted == 0:
            await message.answer("Не удалось удалить указанные файлы.")
        elif deleted == 1:
            await message.answer("Удалён 1 файл.")
        else:
            await message.answer(f"Удалено файлов: {deleted}.")
        return

    await message.answer("Неизвестная подкоманда для /data.")
@router.message()
async def handle_data_import(message: types.Message):
    if not message.document:
        return

    caption = message.caption or ""
    if not caption:
        return

    parts = caption.split(maxsplit=2)
    first = parts[0] if parts else ""
    if first not in {"/data", "/data@" + (message.bot.username or "")}:
        return
    if len(parts) < 2 or parts[1].lower() != "import":
        return

    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    filename = message.document.file_name or "file.bin"
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