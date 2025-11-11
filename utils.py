import os
from typing import Optional

def getenv_str(name: str, default: Optional[str]=None) -> Optional[str]:
    val = os.getenv(name, default)
    return val.strip() if isinstance(val, str) else val

def getenv_int(name: str, default: int) -> int:
    try:
        return int(getenv_str(name, str(default)))
    except Exception:
        return default
