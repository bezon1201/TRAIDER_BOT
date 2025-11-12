# -*- coding: utf-8 -*-
"""
migrations/add_bias_default_long.py — one-off migration to add "bias":"LONG" if missing.
"""
import os, json, glob

def run_migration(storage_dir="storage"):
    os.makedirs(storage_dir, exist_ok=True)
    for path in glob.glob(os.path.join(storage_dir, "*.json")):
        try:
            with open(path, "r+", encoding="utf-8") as f:
                d = json.load(f)
            if "bias" not in d:
                d["bias"] = "LONG"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
                print(f"Updated {os.path.basename(path)}: bias=LONG")
        except Exception as e:
            print(f"Skip {path}: {e}")

if __name__ == "__main__":
    run_migration()
