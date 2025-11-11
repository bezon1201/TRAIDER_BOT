import logging
import os

def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    fmt = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt)
