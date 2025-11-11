
import logging, os, json, sys
from datetime import datetime, timezone

LOG_JSON = os.getenv("LOG_JSON", "false").lower() in ("1","true","yes","y")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "name": record.name,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)

class LineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).isoformat()
        base = f"{ts} | {record.levelname} | {record.name} | {record.getMessage()}"
        if record.exc_info:
            base += " " + self.formatException(record.exc_info)
        return base

def setup_logging() -> logging.Logger:
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    handler.setFormatter(JsonFormatter() if LOG_JSON else LineFormatter())
    logger.addHandler(handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return logging.getLogger("app")
