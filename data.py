import os
import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class DataStorage:
    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"✓ Storage initialized at: {self.storage_path}")

    def get_file_path(self, filename: str):
        file_path = self.storage_path / filename
        if file_path.exists():
            return file_path
        return None

    def get_files_list(self):
        files = [f.name for f in self.storage_path.iterdir() if f.is_file()]
        return sorted(files)

    def save_file(self, filename: str, content):
        file_path = self.storage_path / filename
        if isinstance(content, dict):
            with open(file_path, 'w') as f:
                json.dump(content, f, indent=2)
        else:
            with open(file_path, 'w') as f:
                f.write(str(content))
        logger.info(f"✓ File saved: {filename}")

    def read_file(self, filename: str):
        file_path = self.get_file_path(filename)
        if not file_path:
            return None
        try:
            with open(file_path, 'r') as f:
                if filename.endswith('.json'):
                    return json.load(f)
                return f.read()
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            return None

    def delete_file(self, filename: str) -> bool:
        file_path = self.get_file_path(filename)
        if file_path:
            try:
                file_path.unlink()
                logger.info(f"✓ File deleted: {filename}")
                return True
            except Exception as e:
                logger.error(f"Error deleting {filename}: {e}")
                return False
        return False

    def delete_all(self) -> bool:
        try:
            for f in self.storage_path.iterdir():
                if f.is_file():
                    f.unlink()
            logger.info("✓ All files deleted")
            return True
        except Exception as e:
            logger.error(f"Error deleting all files: {e}")
            return False
