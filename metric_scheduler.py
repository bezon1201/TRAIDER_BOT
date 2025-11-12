import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from collector import collect_all_metrics
from metrics import read_pairs
from market_calculation import force_market_mode

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "metric_scheduler_confyg.json"
LOG_FILENAME = "metric_scheduler.jsonl"

DEFAULT_PERIOD = 3600          # сек
MIN_PERIOD = 900
MAX_PERIOD = 86400

DEFAULT_PUBLISH_HOURS = 24     # часы
MIN_PUBLISH_HOURS = 1
MAX_PUBLISH_HOURS = 96

_state: Dict[str, Any] = {
    "storage_dir": None,
    "period": DEFAULT_PERIOD,
    "publish_hours": DEFAULT_PUBLISH_HOURS,
    "enabled": True,
    "last_publish": None,
    "task": None,
}


def _clamp_period(value: int) -> int:
    try:
        v = int(value)
    except Exception:
        return DEFAULT_PERIOD
    if v < MIN_PERIOD:
        return MIN_PERIOD
    if v > MAX_PERIOD:
        return MAX_PERIOD
    return v


def _clamp_publish_hours(value: int) -> int:
    try:
        v = int(value)
    except Exception:
        return DEFAULT_PUBLISH_HOURS
    if v < MIN_PUBLISH_HOURS:
        return MIN_PUBLISH_HOURS
    if v > MAX_PUBLISH_HOURS:
        return MAX_PUBLISH_HOURS
    return v


def _get_storage_path() -> Optional[Path]:
    storage_dir = _state.get("storage_dir")
    if not storage_dir:
        return None
    return Path(storage_dir)


def _log_event(event_type: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """
    Лог в metric_scheduler.jsonl + обычный логгер.
    """
    storage_path = _get_storage_path()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "message": message,
    }
    if extra:
        record["extra"] = extra
    logger.info(f"[metric_scheduler] {event_type}: {message} | {extra or {}}")
    if not storage_path:
        return
    try:
        log_file = storage_path / LOG_FILENAME
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Error writing metric_scheduler log: {e}")


def _save_config() -> None:
    storage_path = _get_storage_path()
    if not storage_path:
        return
    cfg_path = storage_path / CONFIG_FILENAME
    data = {
        "P": int(_state.get("period", DEFAULT_PERIOD)),
        "N": int(_state.get("publish_hours", DEFAULT_PUBLISH_HOURS)),
    }
    try:
        tmp_path = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp_path.replace(cfg_path)
        _log_event("config_saved", "Config saved", data)
    except Exception as e:
        logger.error(f"Error saving metric_scheduler config: {e}")


def _load_or_init_config() -> None:
    storage_path = _get_storage_path()
    if not storage_path:
        logger.error("No storage_dir for metric_scheduler")
        return
    cfg_path = storage_path / CONFIG_FILENAME
    data: Dict[str, Any] = {}
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Error reading metric_scheduler config, using defaults: {e}")
            data = {}

    period = _clamp_period(data.get("P", DEFAULT_PERIOD))
    publish_hours = _clamp_publish_hours(data.get("N", DEFAULT_PUBLISH_HOURS))

    _state["period"] = period
    _state["publish_hours"] = publish_hours
    # enabled при старте всегда ON
    _state["enabled"] = True

    _save_config()
    _log_event("config_loaded", "Config loaded", {"P": period, "N": publish_hours})


async def _maybe_publish_market_modes() -> None:
    """
    Периодическая публикация market_mode:
    если прошло >= N часов + J (1–3 сек) с последней публикации.
    Используем фрейм 12+6 для записи в <symbol>.json.
    """
    storage_path = _get_storage_path()
    if not storage_path:
        _log_event("error", "No storage_dir in _maybe_publish_market_modes")
        return

    now = datetime.now(timezone.utc)
    last_publish: Optional[datetime] = _state.get("last_publish")
    jitter_sec = random.randint(1, 3)
    interval_sec = int(_state.get("publish_hours", DEFAULT_PUBLISH_HOURS)) * 3600 + jitter_sec

    if last_publish is not None:
        delta = (now - last_publish).total_seconds()
        if delta < interval_sec:
            return

    pairs = read_pairs(str(storage_path))
    if not pairs:
        _log_event("publish_skip", "No pairs to publish market_mode")
        _state["last_publish"] = now
        return

    for symbol in pairs:
        try:
            result = force_market_mode(str(storage_path), symbol, "12+6")
            _log_event(
                "publish",
                f"market_mode published for {symbol}",
                {"symbol": symbol, "frame": "12+6", "result": result},
            )
        except Exception as e:
            logger.error(f"Error publishing market_mode for {symbol}: {e}")
            _log_event("error", f"Error publishing market_mode for {symbol}: {e}")

    _state["last_publish"] = now
    _log_event("publish_done", "market_mode published for all pairs", {"pairs": len(pairs)})


async def _run_loop() -> None:
    """
    Основной цикл планировщика:
    каждые P + J сек собирает метрики и рассчитывает raw состояния.
    """
    storage_path = _get_storage_path()
    if not storage_path:
        logger.error("metric_scheduler: storage_dir is not set, stopping loop")
        _log_event("error", "storage_dir is not set, scheduler stopped")
        return

    _log_event(
        "scheduler_start",
        "Metric scheduler started",
        {"P": _state.get("period"), "N": _state.get("publish_hours")},
    )

    while True:
        try:
            if not _state.get("enabled", True):
                # Планировщик выключен, но цикл живой
                await asyncio.sleep(5)
                continue

            period = int(_state.get("period", DEFAULT_PERIOD))
            jitter_cycle = random.randint(1, 3)

            _log_event(
                "cycle_start",
                "Collecting metrics",
                {"period": period, "jitter": jitter_cycle},
            )

            results = await collect_all_metrics(str(storage_path), delay_ms=50)
            success = sum(1 for v in results.values() if v)
            total = len(results)
            _log_event(
                "cycle_done",
                "Metrics collected",
                {"success": success, "total": total},
            )

            # Рутинная публикация market_mode
            await _maybe_publish_market_modes()

            await asyncio.sleep(max(1, period + jitter_cycle))
        except Exception as e:
            logger.exception("Metric scheduler loop error")
            _log_event("error", f"Scheduler loop error: {e}")
            # Если что-то пошло совсем не так, не падаем, а даём паузу
            await asyncio.sleep(10)


def start_scheduler(storage_dir: str) -> None:
    """
    Инициализация и запуск планировщика.
    Вызывается один раз при старте бота.
    """
    if not storage_dir:
        logger.error("metric_scheduler: empty storage_dir")
        return

    _state["storage_dir"] = storage_dir
    _load_or_init_config()

    existing_task: Optional[asyncio.Task] = _state.get("task")
    if existing_task and not existing_task.done():
        return

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    task = loop.create_task(_run_loop(), name="metric_scheduler_loop")
    _state["task"] = task
    _log_event("scheduler_created", "Scheduler task created", {})


def get_status() -> Dict[str, Any]:
    """
    Статус для команды /scheduler confyg.
    """
    task: Optional[asyncio.Task] = _state.get("task")
    running = bool(task and not task.done())
    if not running:
        logger.error("metric_scheduler task is not running")
        _log_event("error", "metric_scheduler task is not running")

    last_publish: Optional[datetime] = _state.get("last_publish")
    return {
        "period": int(_state.get("period", DEFAULT_PERIOD)),
        "publish_hours": int(_state.get("publish_hours", DEFAULT_PUBLISH_HOURS)),
        "enabled": bool(_state.get("enabled", True)),
        "running": running,
        "last_publish": last_publish.isoformat() if last_publish else None,
    }


def set_period(period: int) -> None:
    """
    /scheduler period <P>
    """
    new_period = _clamp_period(period)
    _state["period"] = new_period
    _save_config()
    _log_event("config_update", "Period updated", {"P": new_period})


def set_publish_hours(hours: int) -> None:
    """
    /scheduler publish <N>
    """
    new_hours = _clamp_publish_hours(hours)
    _state["publish_hours"] = new_hours
    _save_config()
    _log_event("config_update", "Publish hours updated", {"N": new_hours})


def set_enabled(enabled: bool) -> None:
    """
    /scheduler on | off
    """
    _state["enabled"] = bool(enabled)
    _log_event("config_update", "Scheduler enabled changed", {"enabled": bool(enabled)})
