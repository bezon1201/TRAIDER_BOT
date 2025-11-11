from typing import Tuple
from metrics_runner import collect_all_with_micro_jitter, collect_selected_with_micro_jitter
from symbol_info import build_symbol_message

async def run_now(only_symbol: str | None = None) -> Tuple[int, str]:
    if only_symbol:
        sym = (only_symbol or '').upper().strip()
        count = await collect_selected_with_micro_jitter([sym])
        msg = build_symbol_message(sym)
        return count, msg
    count = await collect_all_with_micro_jitter()
    msg = f'Обновлено: {count}' if count else 'Нет пар для обновления'
    return count, msg
