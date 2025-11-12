import os
import json
import logging
import asyncio
import random
import atexit
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

SCHEDULER_LOCK = None
SCHEDULER_TASK = None

def get_config_path(storage_path: str):
    return Path(storage_path) / "metric_scheduler_confyg.json"

def get_lock_path(storage_path: str):
    return Path(storage_path) / "metric_scheduler.lock"

def get_config(storage_path: str):
    config_path = get_config_path(storage_path)
    if config_path.exists():
        with open(config_path, 'r') as f:
            return json.load(f)
    return {"enabled": True, "period_seconds": 3600, "publish_hours": 24, "last_publish": None}

def save_config(storage_path: str, config: dict):
    config_path = get_config_path(storage_path)
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    logger.info("✓ Config saved")

def set_scheduler_enabled(storage_path: str, enabled: bool):
    cfg = get_config(storage_path)
    cfg["enabled"] = enabled
    save_config(storage_path, cfg)
    return True

def set_scheduler_period(storage_path: str, period: int):
    if period < 900 or period > 86400:
        return False
    cfg = get_config(storage_path)
    cfg["period_seconds"] = period
    save_config(storage_path, cfg)
    return True

def set_scheduler_publish(storage_path: str, hours: int):
    if hours < 1 or hours > 96:
        return False
    cfg = get_config(storage_path)
    cfg["publish_hours"] = hours
    save_config(storage_path, cfg)
    return True

def _cleanup_lock():
    global SCHEDULER_LOCK
    if SCHEDULER_LOCK and SCHEDULER_LOCK.exists():
        try:
            SCHEDULER_LOCK.unlink()
            logger.info("✓ Lock cleaned up on exit")
        except:
            pass

async def start_scheduler(storage_path: str):
    global SCHEDULER_TASK, SCHEDULER_LOCK

    lock_path = get_lock_path(storage_path)

    if lock_path.exists():
        try:
            lock_path.unlink()
            logger.info("✓ Old lock removed")
        except:
            logger.warning("Could not remove old lock")

    cfg = get_config(storage_path)
    save_config(storage_path, cfg)

    try:
        with open(lock_path, 'w') as f:
            f.write(str(os.getpid()))
        logger.info("✓ Lock acquired")
    except Exception as e:
        logger.error(f"Lock error: {e}")
        return

    SCHEDULER_LOCK = lock_path
    atexit.register(_cleanup_lock)

    SCHEDULER_TASK = asyncio.create_task(_scheduler_loop(storage_path))
    logger.info("🚀 Scheduler started")

async def _scheduler_loop(storage_path: str):
    from collector import collect_all_metrics
    from market_calculation import force_market_mode
    from metrics import read_pairs

    while True:
        try:
            cfg = get_config(storage_path)
            if not cfg.get("enabled", True):
                await asyncio.sleep(60)
                continue

            logger.info("🔄 Collecting metrics...")
            await collect_all_metrics(storage_path, delay_ms=50)

            logger.info("📊 Publishing market_mode...")
            pairs = read_pairs(storage_path)
            for pair in pairs:
                force_market_mode(storage_path, pair, "12+6")
                force_market_mode(storage_path, pair, "4+2")

            cfg["last_publish"] = datetime.utcnow().isoformat() + "Z"
            save_config(storage_path, cfg)
            logger.info("✓ Market mode published")

            period = cfg.get("period_seconds", 3600)
            jitter = random.randint(1, 3)
            wait_time = period + jitter
            logger.info(f"⏱ Waiting {wait_time}s until next cycle...")

            await asyncio.sleep(wait_time)
        except asyncio.CancelledError:
            logger.info("⛔ Scheduler cancelled")
            break
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            await asyncio.sleep(60)

def stop_scheduler():
    global SCHEDULER_TASK, SCHEDULER_LOCK

    if SCHEDULER_TASK:
        SCHEDULER_TASK.cancel()
        SCHEDULER_TASK = None

    if SCHEDULER_LOCK and SCHEDULER_LOCK.exists():
        try:
            SCHEDULER_LOCK.unlink()
            logger.info("✓ Lock released")
        except:
            pass
