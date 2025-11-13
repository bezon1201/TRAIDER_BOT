import os
import logging
import asyncio
import random
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import os, json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Tuple
from metrics import read_pairs, get_symbol_mode, set_market_mode

def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return out

def _parse_iso(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace("Z","+00:00"))
    except Exception:
        return datetime.now(timezone.utc) - timedelta(days=365*10)

def _tally_market_mode_from_raw(storage_dir: str, symbol: str, mode: str, since: datetime) -> str:
    raw_name = f"{symbol}_raw_market_{mode.upper()}.jsonl"
    raw_path = os.path.join(storage_dir, raw_name)
    rows = _read_jsonl(raw_path)
    # filter by timestamp >= since
    votes = []
    for r in rows:
        ts = r.get("timestamp")
        sig = r.get("signal")
        if not ts or not sig:
            continue
        if _parse_iso(ts) >= since:
            votes.append(sig.upper())
    # If no rows in window -> fallback to last row signal if exists
    if not votes and rows:
        votes = [rows[-1].get("signal","").upper()]
    if not votes:
        return "RANGE"  # safe default

    total = len(votes)
    up = votes.count("UP")
    down = votes.count("DOWN")
    rng = votes.count("RANGE")

    # 60% threshold
    def has60(n): 
        return n/total >= 0.6

    if has60(up):
        return "UP"
    if has60(down):
        return "DOWN"
    if has60(rng):
        return "RANGE"

    # Otherwise choose simple majority (tie -> RANGE)
    if up>=down and up>=rng:
        return "UP"
    if down>=up and down>=rng:
        return "DOWN"
    return "RANGE"


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
                            for frame in ["12+6", "4+2"]:
                                try:
                                    market_mode = force_market_mode(self.storage_dir, symbol, frame)
                                    self._log_event("scheduler_market_mode_published", symbol=symbol, frame=frame, mode=market_mode)
                                    published_count += 1
                                except Exception as e:
                                    self._log_event("scheduler_publish_error", symbol=symbol, frame=frame, error=str(e))
                                    logger.error(f"Scheduler publish error {symbol} {frame}: {e}")
                        
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

def _load_publish_state(storage_dir: str) -> Dict[str, Any]:
    path = os.path.join(storage_dir, "metric_scheduler_config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_publish_state(storage_dir: str, data: Dict[str, Any]) -> None:
    path = os.path.join(storage_dir, "metric_scheduler_config.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def _run_publish_cycle(storage_dir: str, now_dt: datetime) -> Tuple[int,int]:
    cfg = _load_publish_state(storage_dir)
    # read publish period hours (default 24) from config if exists
    publish_hours = cfg.get("publish_hours", 24)
    last_pub_iso = cfg.get("last_publish_ts")
    if last_pub_iso:
        last_pub_dt = _parse_iso(last_pub_iso)
    else:
        last_pub_dt = now_dt - timedelta(hours=publish_hours)
    if now_dt - last_pub_dt < timedelta(hours=publish_hours):
        return (0,0)  # not time yet

    pairs = read_pairs(storage_dir)
    updated = 0
    total = 0
    for sym in pairs:
        mode = get_symbol_mode(storage_dir, sym)
        mm = _tally_market_mode_from_raw(storage_dir, sym, mode or "LONG", since=last_pub_dt)
        try:
            set_market_mode(storage_dir, sym, mm)
            updated += 1
        except Exception:
            pass
        total += 1
    # update last_publish_ts
    cfg["publish_hours"] = publish_hours
    cfg["last_publish_ts"] = now_dt.isoformat()
    _save_publish_state(storage_dir, cfg)
    logger.info(f"✓ Publish: wrote market_mode for {updated}/{total} symbols")
    return (updated, total)
