import os
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

class DataStorage:
    """Управление файлами в персистентном хранилище Render Disk"""

    def __init__(self, storage_dir: str):
        self.storage_dir = Path(storage_dir)
        self._ensure_storage_exists()

    def _ensure_storage_exists(self):
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"✓ Storage initialized at: {self.storage_dir}")

    def get_files_list(self) -> List[str]:
        """Получает список файлов"""
        try:
            files = [f.name for f in self.storage_dir.iterdir() if f.is_file()]
            return sorted(files)
        except Exception as e:
            logger.error(f"Error getting files list: {e}")
            return []

    def get_file_path(self, filename: str) -> Optional[Path]:
        """Получает полный путь к файлу"""
        file_path = self.storage_dir / filename
        if file_path.exists() and file_path.is_file():
            return file_path
        return None

    def delete_all(self) -> bool:
        """Удаляет ВСЕ файлы"""
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

    def delete_file(self, filename: str) -> bool:
        """Удаляет конкретный файл"""
        try:
            file_path = self.get_file_path(filename)
            if file_path:
                file_path.unlink()
                logger.info(f"Deleted: {filename}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting {filename}: {e}")
            return False

    def save_file_atomic(self, filename: str, content: bytes) -> bool:
        """Атомарное сохранение файла"""
        try:
            file_path = self.storage_dir / filename
            tmp_path = self.storage_dir / (filename + ".tmp")

            tmp_path.write_bytes(content)
            tmp_path.replace(file_path)

            logger.info(f"✓ File saved: {filename}")
            return True
        except Exception as e:
            logger.error(f"Error saving {filename}: {e}")
            try:
                tmp_path.unlink()
            except:
                pass
            return False

    def save_file(self, filename: str, content: bytes) -> bool:
        """Алиас для атомарного сохранения"""
        return self.save_file_atomic(filename, content)

    def get_file_size(self, filename: str) -> Optional[int]:
        """Получает размер файла"""
        file_path = self.get_file_path(filename)
        return file_path.stat().st_size if file_path else None

    def file_exists(self, filename: str) -> bool:
        """Проверяет существование файла"""
        return self.get_file_path(filename) is not None
