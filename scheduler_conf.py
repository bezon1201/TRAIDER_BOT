import os
import json
import time
import logging
from pathlib import Path
from typing import Any, Dict

STORAGE_DIR = os.environ.get("STORAGE_DIR", ".")
STORAGE_PATH = Path(STORAGE_DIR)

CONFIG_PATH = STORAGE_PATH / "sheduler_confyg.json"
LOG_PATH = STORAGE_PATH / "scheduler.jsonl"


def _log_event(payload: Dict[str, Any]) -> None:
    """Пишем строку JSON в scheduler.jsonl."""
    rec = dict(payload)
    rec.setdefault("ts", int(time.time()))
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        # Лог планировщика не критичен для работы
        pass


def _default_config(market_publish: int) -> Dict[str, Any]:
    return {
        "status": True,
        "period": 900,
        "publish": market_publish,
        "last_publish_ts": 0,
    }


def load_config(market_publish: int) -> Dict[str, Any]:
    """
    Читает конфиг планировщика. Если файла нет или он битый — создаёт дефолтный.
    """
    if not CONFIG_PATH.exists():
        cfg = _default_config(market_publish)
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with CONFIG_PATH.open("w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        _log_event({"event": "config_created", **cfg})
        return cfg

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = _default_config(market_publish)

    # Гарантируем обязательные поля
    defaults = _default_config(market_publish)
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
