import os
from typing import List, Tuple
import httpx
import glob

TELEGRAM_API_BASE = "https://api.telegram.org"

BOT_TOKEN = (os.getenv("TRAIDER_BOT_TOKEN") or "").strip()
STORAGE_DIR = (os.getenv("STORAGE_DIR") or "/mnt/data").strip() or "/mnt/data"


async def tg_send_message(chat_id: str, text: str) -> None:
    if not BOT_TOKEN or not chat_id:
        return
    url = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            await client.post(url, json=payload)
        except Exception:
            pass


async def tg_send_document(chat_id: str, file_path: str, caption: str | None = None) -> None:
    if not BOT_TOKEN or not chat_id:
        return
    if not os.path.isfile(file_path):
        await tg_send_message(chat_id, f"файл не найден: {os.path.basename(file_path)}")
        return
    url = f"{TELEGRAM_API_BASE}/bot{BOT_TOKEN}/sendDocument"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            with open(file_path, "rb") as f:
                files = {"document": (os.path.basename(file_path), f, "application/octet-stream")}
                await client.post(url, data=data, files=files)
        except Exception:
            await tg_send_message(chat_id, f"не удалось отправить файл: {os.path.basename(file_path)}")


def list_storage_files() -> List[str]:
    try:
        entries = os.listdir(STORAGE_DIR)
    except FileNotFoundError:
        return []
    files = []
    for name in entries:
        p = os.path.join(STORAGE_DIR, name)
        if os.path.isfile(p):
            files.append(name)
    return sorted(files)


def normalize_pattern(p: str) -> str:
    p = (p or "").strip()
    if not p or p.startswith("/") or ".." in p:
        return ""
    return p


def resolve_patterns(patterns: List[str]) -> tuple[list[str], list[str]]:
    matched: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()

    for raw in patterns:
        pat = normalize_pattern(raw)
        if not pat:
            invalid.append(raw)
            continue
        full_pat = os.path.join(STORAGE_DIR, pat)
        hits = [p for p in glob.glob(full_pat) if os.path.isfile(p)]
        if not hits:
            invalid.append(raw)
            continue
        for hp in hits:
            if hp not in seen:
                seen.add(hp)
                matched.append(hp)
    return matched, invalid


async def handle_cmd_data(chat_id: str, args: List[str]) -> None:
    if not args:
        files = list_storage_files()
        if not files:
            await tg_send_message(chat_id, "/data — файлов нет")
            return
        out = ["`/data — список файлов:`"]
        out.extend(files)
        await tg_send_message(chat_id, "\n".join(out))
        return

    sub = (args[0] or "").casefold()
    rest = args[1:]

    if sub == "export":
        if not rest:
            await tg_send_message(chat_id, "/data export — укажи имена файлов или шаблоны")
            return
        matched, invalid = resolve_patterns(rest)
        if not matched and invalid:
            await tg_send_message(chat_id, f"/data export — ничего не найдено\nпропущено: {', '.join(invalid)}")
            return
        for fp in matched:
            await tg_send_document(chat_id, fp)
        msg_lines = ["/data export — отправлено:"]
        msg_lines += [os.path.basename(p) for p in matched] if matched else ["(ничего)"]
        if invalid:
            msg_lines.append(f"пропущено: {', '.join(invalid)}")
        await tg_send_message(chat_id, "\n".join(msg_lines))
        return

    if sub == "delete":
        if not rest:
            await tg_send_message(chat_id, "/data delete — укажи имена файлов или шаблоны")
            return
        matched, invalid = resolve_patterns(rest)
        deleted: list[str] = []
        errors: list[str] = []
        for fp in matched:
            try:
                os.remove(fp)
                deleted.append(os.path.basename(fp))
            except Exception:
                errors.append(os.path.basename(fp))
        lines = ["/data delete — итог:"]
        if deleted:
            lines.append("удалено: " + ", ".join(deleted))
        else:
            lines.append("удалено: (ничего)")
        if invalid:
            lines.append("не найдено: " + ", ".join(invalid))
        if errors:
            lines.append("ошибки: " + ", ".join(errors))
        await tg_send_message(chat_id, "\n".join(lines))
        return

    await tg_send_message(chat_id, "Использование:\n/data — список файлов\n/data export <file1> <file2> ...\n/data delete <file1> <file2> ...")
