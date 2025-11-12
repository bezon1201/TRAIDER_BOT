import os
import json
import logging
import asyncio
import random
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

SCHEDULER_TASK = None
ENABLED = True

def get_config_path(storage_path: str):
    return Path(storage_path) / "metric_scheduler_confyg.json"

def get_log_path(storage_path: str):
    return Path(storage_path) / "metric_scheduler.jsonl"

def get_config(storage_path: str):
    config_path = get_config_path(storage_path)
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"enabled": True, "period_seconds": 3600, "publish_hours": 24, "last_publish": None}

def save_config(storage_path: str, config: dict):
    config_path = get_config_path(storage_path)
    try:
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        logger.info("✓ Config saved")
    except Exception as e:
        logger.error(f"Error saving config: {e}")

def log_action(storage_path: str, action: str, status: str, details: str = ""):
    log_path = get_log_path(storage_path)
    try:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "action": action,
            "status": status,
            "details": details
        }
        with open(log_path, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
    except Exception as e:
        logger.error(f"Error logging action: {e}")

def set_scheduler_enabled(storage_path: str, enabled: bool):
    global ENABLED
    cfg = get_config(storage_path)
    cfg["enabled"] = enabled
    ENABLED = enabled
    save_config(storage_path, cfg)
    log_action(storage_path, "scheduler_control", "success", f"enabled={enabled}")
    return True

def set_scheduler_period(storage_path: str, period: int):
    if period < 900 or period > 86400:
        return False
    cfg = get_config(storage_path)
    cfg["period_seconds"] = period
    save_config(storage_path, cfg)
    log_action(storage_path, "set_period", "success", f"period={period}s")
    return True

def set_scheduler_publish(storage_path: str, hours: int):
    if hours < 1 or hours > 96:
        return False
    cfg = get_config(storage_path)
    cfg["publish_hours"] = hours
    save_config(storage_path, cfg)
    log_action(storage_path, "set_publish", "success", f"publish={hours}h")
    return True

async def start_scheduler(storage_path: str):
    global SCHEDULER_TASK, ENABLED

    cfg = get_config(storage_path)
    ENABLED = cfg.get("enabled", True)
    save_config(storage_path, cfg)

    logger.info("📡 Scheduler task created")
    SCHEDULER_TASK = asyncio.create_task(_scheduler_loop(storage_path))
    logger.info("🚀 Scheduler started (no lock)")

async def _scheduler_loop(storage_path: str):
    global ENABLED

    from collector import collect_all_metrics
    from market_calculation import force_market_mode
    from metrics import read_pairs

    while True:
        try:
            cfg = get_config(storage_path)
            ENABLED = cfg.get("enabled", True)

            if not ENABLED:
                await asyncio.sleep(60)
                continue

            logger.info("🔄 Collecting metrics...")
            log_action(storage_path, "collect_start", "info", "")

            try:
                results = await collect_all_metrics(storage_path, delay_ms=50)
                success = sum(1 for v in results.values() if v)
                total = len(results)
                log_action(storage_path, "collect_end", "success", f"{success}/{total}")
                logger.info(f"✓ Metrics: {success}/{total}")
            except Exception as e:
                logger.error(f"Collect error: {e}")
                log_action(storage_path, "collect_end", "error", str(e))

            logger.info("📊 Publishing market_mode...")
            log_action(storage_path, "publish_start", "info", "")

            try:
                pairs = read_pairs(storage_path)
                for pair in pairs:
                    pair = pair.strip().upper()
                    force_market_mode(storage_path, pair, "12+6")
                    force_market_mode(storage_path, pair, "4+2")

                cfg["last_publish"] = datetime.utcnow().isoformat() + "Z"
                save_config(storage_path, cfg)
                log_action(storage_path, "publish_end", "success", f"pairs={len(pairs)}")
                logger.info("✓ Market mode published")
            except Exception as e:
                logger.error(f"Publish error: {e}")
                log_action(storage_path, "publish_end", "error", str(e))

            period = cfg.get("period_seconds", 3600)
            jitter = random.randint(1, 3)
            wait_time = period + jitter
            logger.info(f"⏱ Waiting {wait_time}s until next cycle...")

            await asyncio.sleep(wait_time)

        except asyncio.CancelledError:
            logger.info("⛔ Scheduler cancelled")
            log_action(storage_path, "scheduler_stop", "info", "cancelled")
            break
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            log_action(storage_path, "scheduler_error", "error", str(e))
            await asyncio.sleep(60)

def stop_scheduler():
    global SCHEDULER_TASK

    if SCHEDULER_TASK:
        SCHEDULER_TASK.cancel()
        SCHEDULER_TASK = None
        logger.info("✓ Scheduler stopped")
