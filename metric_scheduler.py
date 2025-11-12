import os
import json
import logging
import asyncio
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from metrics import read_pairs
from collector import collect_all_metrics
from market_calculation import force_market_mode

logger = logging.getLogger(__name__)

SCHEDULER_CONFIG = "metric_scheduler_confyg.json"
SCHEDULER_LOCK = "metric_scheduler.lock"
SCHEDULER_LOG = "metric_scheduler.log"

def setup_logger():
    """Настройка логирования для планировщика"""
    file_handler = logging.FileHandler(SCHEDULER_LOG, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)
    logger.setLevel(logging.INFO)

class MetricSchedulerConfig:
    """Управление конфигурацией планировщика"""
    def __init__(self, storage_dir: str):
        self.storage_dir = Path(storage_dir)
        self.config_path = self.storage_dir / SCHEDULER_CONFIG
        self.default_config = {
            "enabled": True,
            "period_seconds": 3600,
            "publish_hours": 24,
            "last_publish": None
        }
        self._ensure_config()

    def _ensure_config(self):
        """Создать конфиг если не существует"""
        if not self.config_path.exists():
            self.save_config(self.default_config)
            logger.info(f"✓ Config created: {SCHEDULER_CONFIG}")

    def load_config(self) -> Dict[str, Any]:
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return self.default_config

    def save_config(self, config: Dict[str, Any]) -> bool:
        try:
            tmp_path = self.config_path.with_suffix('.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            tmp_path.replace(self.config_path)
            logger.info(f"✓ Config saved")
            return True
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            return False

    def set_enabled(self, enabled: bool) -> bool:
        config = self.load_config()
        config["enabled"] = enabled
        return self.save_config(config)

    def set_period(self, seconds: int) -> bool:
        if not (900 <= seconds <= 86400):
            logger.error(f"Period must be 900-86400")
            return False
        config = self.load_config()
        config["period_seconds"] = seconds
        return self.save_config(config)

    def set_publish_hours(self, hours: int) -> bool:
        if not (1 <= hours <= 96):
            logger.error(f"Publish hours must be 1-96")
            return False
        config = self.load_config()
        config["publish_hours"] = hours
        return self.save_config(config)

    def update_last_publish(self) -> bool:
        config = self.load_config()
        config["last_publish"] = datetime.now(timezone.utc).isoformat()
        return self.save_config(config)

class MetricScheduler:
    """Планировщик сбора метрик"""
    def __init__(self, storage_dir: str):
        self.storage_dir = storage_dir
        self.config = MetricSchedulerConfig(storage_dir)
        self.lock_path = Path(storage_dir) / SCHEDULER_LOCK
        self.is_running = False

    def _acquire_lock(self) -> bool:
        """Получить lock файл"""
        try:
            if self.lock_path.exists():
                logger.error("⚠ Scheduler already running (lock exists)")
                return False
            self.lock_path.write_text("locked")
            logger.info("✓ Lock acquired")
            return True
        except Exception as e:
            logger.error(f"Error acquiring lock: {e}")
            return False

    def _release_lock(self):
        """Освободить lock файл"""
        try:
            if self.lock_path.exists():
                self.lock_path.unlink()
            logger.info("✓ Lock released")
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")

    def _get_jitter(self) -> int:
        """Джитер 1-3 сек для случайного смещения"""
        return random.randint(1, 3)

    def _should_publish(self) -> bool:
        """Проверить пора ли публиковать market_mode"""
        config = self.config.load_config()
        last_publish = config.get("last_publish")
        publish_hours = config.get("publish_hours", 24)

        if not last_publish:
            return True

        try:
            last_ts = datetime.fromisoformat(last_publish)
            now = datetime.now(timezone.utc)
            diff_hours = (now - last_ts).total_seconds() / 3600
            return diff_hours >= publish_hours
        except:
            return True

    async def run(self):
        """Запустить планировщик"""
        if not self._acquire_lock():
            return

        self.is_running = True
        logger.info("🚀 Scheduler started")

        try:
            while self.is_running:
                config = self.config.load_config()

                # Проверка enabled
                if not config.get("enabled", True):
                    logger.info("⏸ Scheduler disabled, waiting...")
                    await asyncio.sleep(10)
                    continue

                period = config.get("period_seconds", 3600)
                jitter = self._get_jitter()

                try:
                    # Сбор метрик (тихо)
                    logger.info(f"🔄 Collecting metrics...")
                    results = await collect_all_metrics(self.storage_dir, delay_ms=50)
                    success = sum(1 for v in results.values() if v)
                    total = len(results)
                    logger.info(f"✓ Metrics collected: {success}/{total}")

                    # Проверка для публикации market_mode
                    if self._should_publish():
                        logger.info(f"📊 Publishing market_mode...")
                        pairs = read_pairs(self.storage_dir)
                        for symbol in pairs:
                            force_market_mode(self.storage_dir, symbol, "12+6")
                            force_market_mode(self.storage_dir, symbol, "4+2")
                        self.config.update_last_publish()
                        logger.info(f"✓ Market mode published")

                except Exception as e:
                    logger.error(f"❌ Cycle error: {e}")

                # Ожидание с джитером
                wait_time = period + jitter
                logger.info(f"⏱ Waiting {wait_time}s until next cycle...")
                await asyncio.sleep(wait_time)

        except asyncio.CancelledError:
            logger.info("⏹ Scheduler cancelled")
        except Exception as e:
            logger.error(f"❌ Fatal error: {e}")
        finally:
            self.is_running = False
            self._release_lock()
            logger.info("✓ Scheduler stopped")

    def stop(self):
        """Остановить планировщик"""
        self.is_running = False
        logger.info("⏹ Stop signal sent")

# Глобальный инстанс планировщика
_scheduler: Optional[MetricScheduler] = None
_scheduler_task: Optional[asyncio.Task] = None

async def start_scheduler(storage_dir: str):
    """Запустить планировщик в фоне"""
    global _scheduler, _scheduler_task

    setup_logger()

    if _scheduler_task and not _scheduler_task.done():
        logger.warning("Scheduler already running")
        return

    _scheduler = MetricScheduler(storage_dir)
    config = _scheduler.config.load_config()

    # Принудительно выставляем enabled=true при старте
    if not config.get("enabled", True):
        _scheduler.config.set_enabled(True)

    _scheduler_task = asyncio.create_task(_scheduler.run())
    logger.info("📡 Scheduler task created")

def stop_scheduler():
    """Остановить планировщик"""
    global _scheduler
    if _scheduler:
        _scheduler.stop()

def get_config(storage_dir: str) -> Dict[str, Any]:
    """Получить текущий конфиг"""
    config = MetricSchedulerConfig(storage_dir)
    return config.load_config()

def set_scheduler_enabled(storage_dir: str, enabled: bool) -> bool:
    """Управление enabled"""
    config = MetricSchedulerConfig(storage_dir)
    return config.set_enabled(enabled)

def set_scheduler_period(storage_dir: str, seconds: int) -> bool:
    """Установить период сбора метрик"""
    config = MetricSchedulerConfig(storage_dir)
    return config.set_period(seconds)

def set_scheduler_publish(storage_dir: str, hours: int) -> bool:
    """Установить период публикации market_mode"""
    config = MetricSchedulerConfig(storage_dir)
    return config.set_publish_hours(hours)
