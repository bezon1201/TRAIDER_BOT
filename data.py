import os
from typing import List

def _ls(storage_dir: str) -> str:
    try:
        entries = sorted(os.listdir(storage_dir))
    except Exception:
        entries = []
    rows = []
    total = 0
    for name in entries:
        p = os.path.join(storage_dir, name)
        try:
            if os.path.isfile(p):
                sz = os.path.getsize(p)
                rows.append(f"{name:30} {sz:10d} B")
                total += sz
            elif os.path.isdir(p):
                rows.append(f"{name:30} <DIR>")
        except Exception:
            rows.append(f"{name:30} ?")
    head = f"{'Файл':30} {'Размер':>10}
" + "-"*44
    body = "\n".join(rows) if rows else "(пусто)"
    return f"{head}\n{body}\n{'-'*44}\nВсего: {total} B"

def handle_cmd_data(storage_dir: str, args: List[str]) -> str:
    if not args:
        return "/data — список файлов в STORAGE_DIR\n\n" + _ls(storage_dir)
    sub = (args[0] or "").casefold()
    rest = args[1:]
    if sub == "delete":
        if not rest:
            return "/data delete <file1> <file2> ..."
        deleted = []
        skipped = []
        for name in rest:
            p = os.path.join(storage_dir, name)
            try:
                if os.path.isfile(p):
                    os.remove(p)
                    deleted.append(name)
                else:
                    skipped.append(name)
            except Exception:
                skipped.append(name)
        lines = ["/data delete — результат:"]
        if deleted:
            lines.append("удалено: " + ", ".join(deleted))
        if skipped:
            lines.append("пропущено: " + ", ".join(skipped))
        lines.append("")
        lines.append(_ls(storage_dir))
        return "\n".join(lines)
    if sub == "export":
        if not rest:
            return "/data export <file1> <file2> ... (отправка файлов реализуется на стороне отправителя)"
        # Передача файлов делается вызвавшей стороной; здесь просто эхо-список.
        lines = ["/data export — запрошены файлы: " + ", ".join(rest)]
        lines.append("Список в каталоге:")
        lines.append(_ls(storage_dir))
        return "\n".join(lines)
    # default help
    return "\n".join([
        "/data — показать список",
        "/data export <file1> <file2> ... — подготовить к отправке",
        "/data delete <file1> <file2> ... — удалить",
    ])
