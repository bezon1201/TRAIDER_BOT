import os
import re
from pathlib import Path
from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile
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

def validate_names(names: list[str]) -> tuple[list[str], list[str]]:
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

    # /data -> just list
    if not raw:
        return await msg.answer(mono(fmt_dir_listing(d)))

    # first token is subcommand, rest is file list
    parts = raw.split(maxsplit=1)
    sub = (parts[0] or "").casefold()
    rest = parts[1] if len(parts) > 1 else ""

    if sub == "export":
        # 'all' or explicit list
        files = list_files(d) if rest.strip().casefold() == "all" else parse_csv_args(rest)
        ok, bad = validate_names(files)

        sent, skipped = [], []
        for name in ok:
            path = d / name
            try:
                if path.is_file():
                    await msg.answer_document(FSInputFile(path), caption=mono(name))
                    sent.append(name)
                else:
                    skipped.append(name)
            except Exception:
                skipped.append(name)

        # Only report problems (invalid or missing)
        problems = bad + skipped
        if problems:
            await msg.answer(mono("пропущено: " + ", ".join(problems)))
        return

    if sub == "delete":
        files = list_files(d) if rest.strip().casefold() == "all" else parse_csv_args(rest)
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
        text = "\n".join(lines) if lines else "удалено: —"
        return await msg.answer(mono(text))

    # help
    help_text = [
        "Использование:",
        "/data — показать список файлов (через запятую)",
        "/data export all — отправить все файлы",
        "/data export file1.ext, file2.ext — отправить выбранные",
        "/data delete all — удалить все файлы",
        "/data delete file1.ext, file2.ext — удалить выбранные",
    ]
    return await msg.answer(mono("\n".join(help_text)))
