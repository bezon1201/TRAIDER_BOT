from typing import Tuple, Optional, List
from metrics_runner import collect_all_with_micro_jitter, collect_selected_with_micro_jitter, load_pairs
from range_mode import get_mode

async def run_now(filter_mode: Optional[str] = None) -> Tuple[int, str]:
    """Run /now collection.
    When filter_mode is 'LONG' or 'SHORT' (case-insensitive), only those pairs are collected.
    Returns (count, message).
    """
    if not filter_mode:
        count = await collect_all_with_micro_jitter()
        msg = f"Обновлено: {count}" if count else "Нет пар для обновления"
        return count, msg

    mode = (filter_mode or "").strip().upper()
    if mode not in ("LONG","SHORT"):
        count = await collect_all_with_micro_jitter()
        msg = f"Обновлено: {count}" if count else "Нет пар для обновления"
        return count, msg

    pairs: List[str] = load_pairs()
    selected = [s for s in (pairs or []) if get_mode(s)[1] == mode]
    if not selected:
        return 0, "Нет пар для обновления"
    count = await collect_selected_with_micro_jitter(selected)
    msg = f"Обновлено: {count}" if count else "Нет пар для обновления"
    return count, msg
