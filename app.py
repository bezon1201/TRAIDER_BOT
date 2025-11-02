
    import os, json, asyncio
    from typing import Dict, Any, List
    from fastapi import FastAPI, Request
    import httpx
    from .utils import STORAGE_DIR, load_pairs, read_json, write_json, get_modes
    from .range_mode import get_all_modes, set_pair_mode
    from .market_mode import market_mode_from_snap
    from .oco_calc import compute_oco_buy
    from .oco_params import adaptive_params as _adaptive_params

    app = FastAPI(title="Trade Bot v31")

    TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")

    async def tg_send(chat_id: int, text: str):
        if not TG_BOT_TOKEN:
            return
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

    async def tg_send_document(chat_id: int, filename: str, content: bytes):
        if not TG_BOT_TOKEN:
            return
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendDocument"
        files = {"document": (os.path.basename(filename), content)}
        data = {"chat_id": str(chat_id)}
        async with httpx.AsyncClient(timeout=20.0) as client:
            await client.post(url, data=data, files=files)

    def _code(s: str) -> str:
        return f"""```
{s}
```"""

    @app.get("/health")
    def health():
        return {"ok": True, "version": 31}

    @app.post("/tg")
    async def telegram_webhook(req: Request):
        payload = await req.json()
        msg = (payload.get("message") or payload.get("edited_message") or {})
        chat_id = (((msg.get("chat") or {}) ).get("id")) or 0
        text = (msg.get("text") or "").strip()
        if not chat_id or not text:
            return {"ok": True}

        # /now — recompute levels for LONG pairs (no external fetch; compute from existing snaps)
        if text.startswith("/now"):
            pairs = load_pairs()
            modes = get_modes()
            updated = []
            for sym in pairs:
                path = os.path.join(STORAGE_DIR, f"{sym}.json")
                snap = read_json(path)
                # attach trade_mode from modes (default SHORT, per your earlier rule)
                trade_mode = (modes.get(sym) or "SHORT").upper()
                snap["symbol"] = snap.get("symbol", {"name": sym, "tickSize": snap.get("tickSize", 0.01)})
                snap["trade_mode"] = trade_mode
                # compute market mode label (non-blocking heuristic)
                mm = market_mode_from_snap(snap)
                snap.setdefault("market", {})["mode_12h"] = mm
                # compute OCO only if LONG
                oco = {}
                if trade_mode == "LONG":
                    try:
                        oco = compute_oco_buy(snap, _adaptive_params)
                    except Exception:
                        oco = {}
                if oco:
                    levels = snap.setdefault("levels", {}).setdefault("buy", {})
                    h = levels.setdefault("12h", {})
                    h["TP Limit"] = oco["TP Limit"]
                    h["SL Trigger"] = oco["SL Trigger"]
                    h["SL Limit"] = oco["SL Limit"]
                    # params snapshot
                    p = snap.setdefault("levels", {}).setdefault("params", {}).setdefault("12h", {})
                    ap = _adaptive_params(snap)
                    p["band_low"] = ap.get("band_low", 0.0)
                    p["band_high"] = ap.get("band_high", 0.0)
                    p["tickSize"] = oco.get("tickSize", 0.01)
                snap["updated_at"] = __import__("datetime").datetime.utcnow().isoformat() + "Z"
                write_json(path, snap)
                updated.append(sym)
            await tg_send(chat_id, _code("OK /now — обновлено: " + ", ".join(updated)))
            return {"ok": True}

        # /market — list modes with trade mode
        if text.startswith("/market"):
            parts = text.split()
            pairs = load_pairs()
            modes = get_modes()
            def line(sym: str) -> str:
                snap = read_json(os.path.join(STORAGE_DIR, f"{sym}.json"))
                mm = market_mode_from_snap(snap)
                icon = {"UP":"⬆️","DOWN":"⬇️","RANGE":"🔄"}.get(mm, "🔄")
                tmode = (modes.get(sym) or "AUTO").upper()
                ticon = {"LONG":"📈","SHORT":"📉","AUTO":"🤖"}.get(tmode, "🤖")
                return f"{sym} {mm}{icon} Mode {tmode}{ticon}"
            if len(parts) == 1:
                lines = [line(sym) for sym in pairs]
                await tg_send(chat_id, _code("\n".join(lines)))
                return {"ok": True}
            else:
                sym = parts[1].upper()
                await tg_send(chat_id, _code(line(sym)))
                return {"ok": True}

        # /mode commands
        if text.startswith("/mode"):
            parts = text.split()
            if len(parts) == 1:
                # silent per your spec: /mode does not answer
                return {"ok": True}
            if len(parts) == 2:
                # show single
                sym = parts[1].upper()
                state = get_all_modes().get(sym, "AUTO")
                await tg_send(chat_id, _code(f"{sym}: {state}"))
                return {"ok": True}
            # set
            sym = parts[1].upper()
            mode = parts[2].upper()
            new_state = set_pair_mode(sym, mode)
            await tg_send(chat_id, _code(f"{sym}: {new_state}"))
            return {"ok": True}

        # /json — list or send file
        if text.startswith("/json"):
            parts = text.split()
            if len(parts) == 1:
                files = []
                for p in load_pairs():
                    fp = os.path.join(STORAGE_DIR, f"{p}.json")
                    if os.path.exists(fp):
                        files.append(os.path.basename(fp))
                if not files:
                    await tg_send(chat_id, _code("Нет файлов"))
                else:
                    await tg_send(chat_id, _code("\n".join(sorted(files))))
                return {"ok": True}
            else:
                sym = parts[1].upper()
                fp = os.path.join(STORAGE_DIR, f"{sym}.json")
                if not os.path.exists(fp):
                    await tg_send(chat_id, _code("Файл не найден"))
                    return {"ok": True}
                content = open(fp, "rb").read()
                await tg_send_document(chat_id, os.path.basename(fp), content)
                return {"ok": True}

        # /levels <PAIR> — on-demand compute and show
        if text.startswith("/levels"):
            parts = text.split()
            if len(parts) == 1:
                await tg_send(chat_id, _code("Использование: /levels <PAIR>"))
                return {"ok": True}
            sym = parts[1].upper()
            path = os.path.join(STORAGE_DIR, f"{sym}.json")
            if not os.path.exists(path):
                await tg_send(chat_id, _code("Файл не найден"))
                return {"ok": True}
            snap = read_json(path)
            try:
                oco = compute_oco_buy(snap, _adaptive_params)
            except Exception:
                oco = {}
            if not oco:
                await tg_send(chat_id, _code("Нет уровней (только для LONG или нет данных)"))
                return {"ok": True}
            msg = f"TP Limit {oco['TP Limit']:.8f}\nSL Trigger {oco['SL Trigger']:.8f}\nSL Limit {oco['SL Limit']:.8f}"
            await tg_send(chat_id, _code(msg))
            return {"ok": True}

        # /portfolio — placeholder echo
        if text.startswith("/portfolio"):
            await tg_send(chat_id, _code("Портфель пока не настроен в v31"))
            return {"ok": True}

        # default
        await tg_send(chat_id, _code("Неизвестная команда"))
        return {"ok": True}
