import os
import re
from pathlib import Path
from aiogram import Router, types
from utils import mono

router = Router()

SAFE_NAME_RE = r"^[^/\\\0]+$"  # simple guard: no slashes or nulls

def ensure_storage_dir(base: str | None = None) -> Path:
    d = Path(base or os.getenv("STORAGE_DIR") or "./storage")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d

def fmt_dir_listing(d: Path) -> str:
    try:
        names = sorted(os.listdir(d))
    except Exception:
        names = []
    rows = []
    total = 0
    for name in names:
        p = d / name
        if p.is_file():
            try:
                sz = p.stat().st_size
                total += sz
                size_s = f"{sz} B"
            except Exception:
                size_s = "?"
        elif p.is_dir():
            size_s = "<DIR>"
        else:
            size_s = "?"
        rows.append((name, size_s))
    if not rows:
        body = "(пусто)"
    else:
        header = "Файл                      Размер"
        sep = "-------------------------  --------"
        lines = [header, sep]
        for name, size_s in rows:
            lines.append(f"{name:<25}  {size_s:>8}")
        lines.append(sep)
        lines.append(f"Всего: {total} B")
        body = "\n".join(lines)
    return body

def validate_names(names):
    ok, bad = [], []
    for n in names:
        if re.match(SAFE_NAME_RE, n or ""):
            ok.append(n)
        else:
            bad.append(n)
    return ok, bad

@router.message(lambda m: isinstance(m.text, str) and m.text.strip().startswith("/data"))
async def cmd_data(msg: types.Message):
    text = msg.text or ""
    parts = text.strip().split()
    args = [p for p in parts[1:] if p]
    d = ensure_storage_dir()

    if not args:
        out = fmt_dir_listing(d)
        return await msg.answer(mono(out))

    sub = (args[0] or "").casefold()

    if sub == "export":
        files = args[1:]
        ok, bad = validate_names(files)
        resp = ["export: заявки приняты"]
        if ok:
            resp.append("файлы: " + ", ".join(ok))
        if bad:
            resp.append("пропущено (некорректные имена): " + ", ".join(bad))
        resp.append("")
        resp.append(fmt_dir_listing(d))
        return await msg.answer(mono("\n".join(resp)))

    if sub == "delete":
        files = args[1:]
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
        resp = [f"delete: удалено {len(deleted)}, пропущено {len(skipped)}"]
        if deleted:
            resp.append("удалено: " + ", ".join(deleted))
        if skipped or bad:
            skipped_all = skipped + ([f"{b}*bad*" for b in bad] if bad else [])
            resp.append("пропущено: " + ", ".join(skipped_all) if skipped_all else "пропущено: —")
        resp.append("")
        resp.append(fmt_dir_listing(d))
        return await msg.answer(mono("\n".join(resp)))

    help_text = [
        "Использование:",
        "/data — показать список в STORAGE_DIR",
        "/data export <file1> <file2> ... — заявка на экспорт",
        "/data delete <file1> <file2> ... — удалить файлы",
    ]
    return await msg.answer(mono("\n".join(help_text)))
