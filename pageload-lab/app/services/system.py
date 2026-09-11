"""System diagnostics exposed to administrators through Ingress."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import Any

import psutil
from sqlalchemy import func, select

from ..models import RequestRecord, Test


class SystemService:
    def __init__(self, database: Any, scheduler: Any, browser: Any, paths: Any, version: str) -> None:
        self.database = database
        self.scheduler = scheduler
        self.browser = browser
        self.paths = paths
        self.version_value = version

    async def info(self) -> dict[str, Any]:
        with self.database.session_factory() as session:
            tests = session.scalar(select(func.count(Test.id))) or 0
            requests = session.scalar(select(func.count(RequestRecord.id))) or 0
        disk = shutil.disk_usage(self.paths.data)
        process = psutil.Process(os.getpid())
        browser_health = await self.browser.health()
        return {
            "version": self.version_value,
            "python_version": platform.python_version(),
            "chromium_version": await self.browser.version(),
            "architecture": platform.machine(),
            "database": self.database.status(),
            "test_count": tests,
            "request_count": requests,
            "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
            "ram": {"process_rss": process.memory_info().rss, "system_percent": psutil.virtual_memory().percent},
            "active_browser_sessions": self.browser.active_sessions,
            "waiting_browser_sessions": self.browser.waiting_sessions,
            "browser_zombie_processes": browser_health["zombie_processes"],
            "browser_last_error": browser_health["last_error"],
            "scheduler": self.scheduler.status(),
        }
