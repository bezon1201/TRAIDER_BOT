
from typing import Tuple
from metrics_runner import collect_all_no_jitter

async def run_now() -> Tuple[int, str]:
    count = await collect_all_no_jitter()
    msg = f"Обновлено: {count}" if count else "Нет пар для обновления"
    return count, msg
