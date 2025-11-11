import os
import re
from pathlib import Path
from aiogram import Router, types
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

def fmt_dir_listing(d: Path) -> str:
    try:
        names = sorted([n for n in os.listdir(d) if (d / n).is_file()])
    except Exception:
        names = []
    if not names:
        return "(пусто)"
    return ", ".join(names)

def parse_csv_args(raw: str):
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
    args = parse_csv_args(raw)
    d = ensure_storage_dir()

    if not args:
        out = fmt_dir_listing(d)
        return await msg.answer(mono(out))

    sub = (args[0] or "").casefold()

    if sub == "export":
        files = parse_csv_args(",".join(args[1:]))
        ok, bad = validate_names(files)
        lines = []
        if ok:
            lines.append("export: " + ", ".join(ok))
        if bad:
            lines.append("пропущено: " + ", ".join(bad))
        text = "\n".join(lines) if lines else "export: (пусто)"
        return await msg.answer(mono(text))

    if sub == "delete":
        files = parse_csv_args(",".join(args[1:]))
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

    help_text = [
        "Использование:",
        "/data — показать список файлов (через запятую)",
        "/data export file1.ext, file2.ext — заявка на экспорт",
        "/data delete file1.ext, file2.ext — удалить файлы",
    ]
    return await msg.answer(mono("\n".join(help_text)))
