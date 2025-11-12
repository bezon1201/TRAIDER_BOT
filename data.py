# -*- coding: utf-8 -*-
"""
data.py — helpers for reading/writing <SYMBOL>.json including bias.
"""
import os, json, glob
from typing import Optional, Dict, Any, List

DEFAULT_STORAGE_DIR = os.environ.get("STORAGE_DIR", "storage")

def get_storage_dir() -> str:
    return DEFAULT_STORAGE_DIR

def _symbol_path(storage_dir: str, symbol: str) -> str:
    os.makedirs(storage_dir, exist_ok=True)
    return os.path.join(storage_dir, f"{symbol}.json")

def load_symbol_json(storage_dir: str, symbol: str) -> Optional[Dict[str, Any]]:
    path = _symbol_path(storage_dir, symbol)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_symbol_json(storage_dir: str, symbol: str, data: Dict[str, Any]) -> None:
    path = _symbol_path(storage_dir, symbol)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def list_symbols(storage_dir: str) -> List[str]:
    os.makedirs(storage_dir, exist_ok=True)
    out = []
    for p in glob.glob(os.path.join(storage_dir, "*.json")):
        base = os.path.basename(p)
        if base.endswith(".json"):
            out.append(base[:-5])
    return sorted(out)

def get_symbol_bias(storage_dir: str, symbol: str) -> str:
    data = load_symbol_json(storage_dir, symbol) or {}
    bias = data.get("bias", "LONG")
    # auto-backfill if missing
    if "bias" not in data:
        data["bias"] = bias
        save_symbol_json(storage_dir, symbol, data)
    return bias
