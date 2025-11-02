
from typing import Dict
from .utils import get_modes, set_mode, load_pairs

VALID = {"AUTO","LONG","SHORT","RESET"}

def get_all_modes() -> Dict[str, str]:
    modes = get_modes()
    pairs = load_pairs()
    out = {}
    for p in pairs:
        out[p] = modes.get(p, "AUTO")
    return out

def set_pair_mode(pair: str, mode: str) -> str:
    m = mode.upper()
    if m not in VALID:
        raise ValueError("invalid mode")
    set_mode(pair.upper(), m)
    return m
