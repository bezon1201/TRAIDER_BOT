import os
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

class DataStorage:
    """Модуль для управления файлами в хранилище"""

    def __init__(self, storage_dir: str):
        """
        Инициализация хранилища

        Args:
            storage_dir: Путь к директории хранилища (переменная DATA_STORAGE)
        """
        self.storage_dir = Path(storage_dir)
        self._ensure_storage_exists()

    def _ensure_storage_exists(self):
        """Создаёт директорию хранилища, если её нет"""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Storage initialized at: {self.storage_dir}")

    def get_files_list(self) -> List[str]:
        """
        Получает список всех файлов в хранилище

        Returns:
            Список имён файлов с расширениями
        """
        try:
            files = [f.name for f in self.storage_dir.iterdir() if f.is_file()]
            return sorted(files)
        except Exception as e:
            logger.error(f"Error getting files list: {e}")
            return []

    def get_file_path(self, filename: str) -> Optional[Path]:
        """
        Получает полный путь к файлу

        Args:
            filename: Имя файла

        Returns:
            Path объект если файл существует, иначе None
        """
        file_path = self.storage_dir / filename
        if file_path.exists() and file_path.is_file():
            return file_path
        logger.warning(f"File not found: {filename}")
        return None

    def delete_all(self) -> bool:
        """
        Удаляет все файлы в хранилище

        Returns:
            True если успешно, False при ошибке
        """
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

    def save_file(self, filename: str, content: bytes) -> bool:
        """
        Сохраняет файл в хранилище

        Args:
            filename: Имя файла
            content: Содержимое файла (байты)

        Returns:
            True если успешно, False при ошибке
        """
        try:
            file_path = self.storage_dir / filename
            file_path.write_bytes(content)
            logger.info(f"File saved: {filename}")
            return True
        except Exception as e:
            logger.error(f"Error saving file {filename}: {e}")
            return False

    def get_file_size(self, filename: str) -> Optional[int]:
        """
        Получает размер файла в байтах

        Args:
            filename: Имя файла

        Returns:
            Размер файла в байтах или None
        """
        file_path = self.get_file_path(filename)
        if file_path:
            return file_path.stat().st_size
        return None

    def file_exists(self, filename: str) -> bool:
        """Проверяет существование файла"""
        return self.get_file_path(filename) is not None
