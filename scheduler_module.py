import asyncio
import logging
from typing import Optional
from aiogram import Router

router = Router()

class Scheduler:
    def __init__(self, storage_dir: str):
        self.storage_dir = storage_dir
        self._task: Optional[asyncio.Task] = None
        self._enabled = False

    def enable(self, loop: asyncio.AbstractEventLoop):
        if self._enabled:
            return
        self._enabled = True
        self._task = loop.create_task(self._runner())

    async def _runner(self):
        log = logging.getLogger("scheduler")
        log.info("scheduler started (stub)")
        await asyncio.sleep(0)  # yield once
