import os
import re
from pathlib import Path
from aiogram import Router, types
from aiogram.types import FSInputFile
from aiogram.filters import Command, CommandObject
from utils import mono

router = Router()

SAFE_NAME_RE = r"^[^/\\\0]+$"  # no slashes or nulls

def ensure_storage_dir(base: str | None = None) -> Path:
    d = Path(base or os.getenv("STORAGE_DIR") or "./storage")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d

def list_files(d: Path) -> list[str]:
    try:
        names = sorted([n for n in os.listdir(d) if (d / n).is_file()])
    except Exception:
        names = []
    return names

def fmt_dir_listing(d: Path) -> str:
    names = list_files(d)
    return "(пусто)" if not names else ", ".join(names)

def parse_csv_args(raw: str) -> list[str]:
    parts = [p.strip() for p in (raw.split(",") if raw else [])]
    return [p for p in parts if p]

def validate_names(names):
    ok, bad = [], []
    for n in names:
        if re.match(SAFE_NAME_RE, n or ""):
            ok.append(n)
        else:
            bad.append(n)
    return ok, bad

@router.message(Command("data"))
async def cmd_data(msg: types.Message, command: CommandObject):
    raw = (command.args or "").strip()
    d = ensure_storage_dir()

    if not raw:
        return await msg.answer(mono(fmt_dir_listing(d)))

    parts = raw.split(maxsplit=1)
    sub = (parts[0] or "").casefold()
    rest = parts[1] if len(parts) > 1 else ""

        if sub == "export":
        # Determine files
        if rest.strip().casefold() == "all":
            files = list_files(d)
        else:
            files = parse_csv_args(rest)
        ok, bad = validate_names(files)

        # Send each existing file as document with short mono caption
        sent, skipped = [], []
        for name in ok:
            path = d / name
            try:
                if path.is_file():
                    doc = FSInputFile(path)
                    await msg.answer_document(document=doc, caption=mono(name))
                    sent.append(name)
                else:
                    skipped.append(name)
            except Exception:
                skipped.append(name)

        # Only report problems; no extra confirmation if all sent
        problems = bad + skipped
        if problems:
            return await msg.answer(mono("пропущено: " + ", ".join(problems)))
        return


    if sub == "delete":
        if rest.strip().casefold() == "all":
            files = list_files(d)
        else:
            files = parse_csv_args(rest)
        ok, bad = validate_names(files)
        deleted, skipped = [], []
        for name in ok:
            p = d / name
            try:
                if p.is_file():
                    p.unlink()
                    deleted.append(name)
                else:
                    skipped.append(name)
            except Exception:
                skipped.append(name)
        lines = []
        if deleted:
            lines.append("удалено: " + ", ".join(deleted))
        if skipped or bad:
            lines.append("пропущено: " + ", ".join(skipped + bad))
        return await msg.answer(mono("\n".join(lines) if lines else "удалено: —"))

    help_text = [
        "Использование:",
        "/data — показать список файлов (через запятую)",
        "/data export all — заявка на экспорт всех файлов",
        "/data export file1.ext, file2.ext — заявка на экспорт конкретных",
        "/data delete all — удалить все файлы",
        "/data delete file1.ext, file2.ext — удалить конкретные файлы",
    ]
    return await msg.answer(mono("\n".join(help_text)))
