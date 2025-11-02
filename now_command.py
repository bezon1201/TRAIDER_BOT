
from typing import Tuple
from metrics_runner import collect_all_now

async def run_now() -> Tuple[int, str]:
    count = await collect_all_now()
    msg = f"Обновлено: {count}" if count else "Нет пар для обновления"
    return count, msg
