import os
import logging
import asyncio
import random
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class MetricScheduler:
    """Асинхронный планировщик для сбора метрик и публикации market_mode"""
    
    def __init__(self, storage_dir: str, config_filename: str = "metric_scheduler_config.json"):
        self.storage_dir = storage_dir
        self.config_path = os.path.join(storage_dir, config_filename)
        self.config: Dict[str, Any] = {}
        self._running = False
        self.load_config()
        self._log_event("scheduler_initialized", version="6.0")
    
    def load_config(self) -> bool:
        """Загрузить конфиг, при ошибке → дефолты"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.config = {
                        "period": max(900, min(86400, data.get("period", 3600))),
                        "publish_interval_hours": max(1, min(96, data.get("publish_interval_hours", 24))),
                        "enabled": data.get("enabled", True),
                        "last_published": data.get("last_published", datetime.now(timezone.utc).isoformat())
                    }
                    logger.info(f"✓ Scheduler config loaded: period={self.config['period']}s, publish={self.config['publish_interval_hours']}h")
                    return True
        except Exception as e:
            logger.error(f"Scheduler config load error: {e}")
        
        self.config = {
            "period": 3600,
            "publish_interval_hours": 24,
            "enabled": True,
            "last_published": datetime.now(timezone.utc).isoformat()
        }
        self.save_config()
        self._log_event("scheduler_config_created", **self.config)
        return True
    
    def save_config(self) -> bool:
        """Сохранить конфиг атомарно"""
        try:
            os.makedirs(self.storage_dir, exist_ok=True)
            tmp_path = self.config_path + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.config_path)
            logger.info(f"✓ Scheduler config saved")
            return True
        except Exception as e:
            logger.error(f"Scheduler config save error: {e}")
            try:
                os.unlink(tmp_path)
            except:
                pass
            return False
    
    def _log_event(self, event_name: str, **kwargs) -> None:
        """Логировать событие в metric_scheduler.jsonl"""
        try:
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event_name,
                **kwargs
            }
            jsonl_path = os.path.join(self.storage_dir, "metric_scheduler.jsonl")
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Scheduler event logging error: {e}")
    
    def _get_jitter(self) -> float:
        """Получить джитер 1-3 сек"""
        return random.uniform(1.0, 3.0)
    
    def get_config(self) -> Dict[str, Any]:
        """Получить текущий конфиг"""
        return self.config.copy()
    
    def update_period(self, new_period: int) -> bool:
        """Обновить период сбора метрик [900…86400]"""
        if not (900 <= new_period <= 86400):
            logger.error(f"Period {new_period} out of range [900…86400]")
            return False
        old_period = self.config["period"]
        self.config["period"] = new_period
        self.save_config()
        self._log_event("scheduler_config_changed", param="period", old_value=old_period, new_value=new_period)
        logger.info(f"✓ Scheduler period changed: {old_period}s → {new_period}s")
        return True
    
    def update_publish_interval(self, new_interval: int) -> bool:
        """Обновить период публикации [1…96] часов"""
        if not (1 <= new_interval <= 96):
            logger.error(f"Publish interval {new_interval} out of range [1…96]")
            return False
        old_interval = self.config["publish_interval_hours"]
        self.config["publish_interval_hours"] = new_interval
        self.save_config()
        self._log_event("scheduler_config_changed", param="publish_interval_hours", old_value=old_interval, new_value=new_interval)
        logger.info(f"✓ Scheduler publish interval changed: {old_interval}h → {new_interval}h")
        return True
    
    def toggle_scheduler(self, enabled: bool) -> bool:
        """Включить/отключить планировщик"""
        old_state = self.config["enabled"]
        self.config["enabled"] = enabled
        self.save_config()
        self._log_event("scheduler_toggled", enabled=enabled, was=old_state)
        logger.info(f"✓ Scheduler toggled: {old_state} → {enabled}")
        return True
    
    async def start_loop(self) -> None:
        """Запустить основной асинхронный цикл"""
        from collector import collect_all_metrics
        from market_calculation import force_market_mode
        from metrics import read_pairs
        
        self._running = True
        self._log_event("scheduler_loop_started")
        logger.info("✓ Scheduler loop started")
        
        try:
            while self._running:
                if not self.config.get("enabled", True):
                    await asyncio.sleep(5)
                    continue
                
                jitter = self._get_jitter()
                period = self.config["period"]
                sleep_time = period + jitter
                
                logger.info(f"Scheduler: sleeping {period}s + jitter {jitter:.1f}s = {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)
                
                if not self._running:
                    break
                
                try:
                    self._log_event("scheduler_collection_start")
                    results = await collect_all_metrics(self.storage_dir, delay_ms=50)
                    success_count = sum(1 for v in results.values() if v)
                    total = len(results)
                    self._log_event("scheduler_collection_complete", success=success_count, total=total)
                    logger.info(f"✓ Scheduler collection: {success_count}/{total} success")
                except Exception as e:
                    self._log_event("scheduler_collection_error", error=str(e))
                    logger.error(f"Scheduler collection error: {e}")
                
                try:
                    now = datetime.now(timezone.utc)
                    last_published = datetime.fromisoformat(self.config["last_published"])
                    hours_passed = (now - last_published).total_seconds() / 3600
                    publish_interval_h = self.config["publish_interval_hours"]
                    jitter_sec = self._get_jitter()
                    
                    if hours_passed >= publish_interval_h + (jitter_sec / 3600):
                        self._log_event("scheduler_publish_check", should_publish=True, hours_passed=round(hours_passed, 2))
                        logger.info(f"Scheduler: publishing market_mode (hours_passed={hours_passed:.1f}h >= {publish_interval_h}h)")
                        
                        pairs = read_pairs(self.storage_dir)
                        published_count = 0
                        
                        for symbol in pairs:
                            try:
                                from metrics import get_symbol_mode
                                mode = get_symbol_mode(self.storage_dir, symbol)
                                if mode in ("LONG", "SHORT"):
                                    market_mode = force_market_mode(self.storage_dir, symbol, mode)
                                    self._log_event("scheduler_market_mode_published", symbol=symbol, frame=mode, mode=market_mode)
                                    published_count += 1
                            except Exception as e:
                                self._log_event("scheduler_publish_error", symbol=symbol, frame=str(mode), error=str(e))
                                logger.error(f"Scheduler publish error {symbol} {mode}: {e}")
                        
                        self.config["last_published"] = now.isoformat()
                        self.save_config()
                        
                        logger.info(f"✓ Scheduler published: {published_count} entries")
                    else:
                        remaining_h = publish_interval_h - hours_passed
                        self._log_event("scheduler_publish_check", should_publish=False, hours_remaining=round(remaining_h, 2))
                        logger.info(f"Scheduler: next publish in {remaining_h:.1f}h")
                        
                except Exception as e:
                    self._log_event("scheduler_publish_check_error", error=str(e))
                    logger.error(f"Scheduler publish check error: {e}")
        
        except asyncio.CancelledError:
            self._log_event("scheduler_loop_cancelled")
            logger.info("Scheduler loop cancelled")
        except Exception as e:
            self._log_event("scheduler_loop_error", error=str(e))
            logger.error(f"Scheduler loop error: {e}")
        finally:
            self._running = False
            self._log_event("scheduler_loop_stopped")
            logger.info("✓ Scheduler loop stopped")
    
    def stop_loop(self) -> None:
        """Остановить цикл"""
        self._running = False
        logger.info("✓ Scheduler stop signal sent")