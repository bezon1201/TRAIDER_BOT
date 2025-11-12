import os
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

class DataStorage:
    """Модуль для управления файлами в хранилище с атомарной записью"""

    def __init__(self, storage_dir: str):
        self.storage_dir = Path(storage_dir)
        self._ensure_storage_exists()

    def _ensure_storage_exists(self):
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Storage initialized at: {self.storage_dir}")

    def get_files_list(self) -> List[str]:
        try:
            files = [f.name for f in self.storage_dir.iterdir() if f.is_file()]
            return sorted(files)
        except Exception as e:
            logger.error(f"Error getting files list: {e}")
            return []

    def get_file_path(self, filename: str) -> Optional[Path]:
        file_path = self.storage_dir / filename
        if file_path.exists() and file_path.is_file():
            return file_path
        return None

    def delete_all(self) -> bool:
        try:
            files = self.get_files_list()
            for filename in files:
                file_path = self.storage_dir / filename
                file_path.unlink()
                logger.info(f"Deleted: {filename}")
            logger.info(f"All files deleted. Total: {len(files)}")
            return True
        except Exception as e:
            logger.error(f"Error deleting files: {e}")
            return False

    def save_file_atomic(self, filename: str, content: bytes) -> bool:
        """Сохраняет файл атомарно через временный файл (как в старом боте)"""
        try:
            file_path = self.storage_dir / filename
            tmp_path = self.storage_dir / (filename + ".tmp")

            # Пишем во временный файл
            tmp_path.write_bytes(content)

            # Атомарная замена
            tmp_path.replace(file_path)

            logger.info(f"File saved atomically: {filename}")
            return True
        except Exception as e:
            logger.error(f"Error saving file {filename}: {e}")
            # Очищаем временный файл если что-то пошло не так
            try:
                tmp_path.unlink()
            except:
                pass
            return False

    def save_file(self, filename: str, content: bytes) -> bool:
        """Алиас для атомарного сохранения"""
        return self.save_file_atomic(filename, content)

    def get_file_size(self, filename: str) -> Optional[int]:
        file_path = self.get_file_path(filename)
        if file_path:
            return file_path.stat().st_size
        return None

    def file_exists(self, filename: str) -> bool:
        return self.get_file_path(filename) is not None
