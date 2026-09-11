"""APScheduler coordination for delayed starts and retention."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .models import Test
from .worker import TestRunnerManager, aware

LOGGER = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, session_factory: Any, runners: TestRunnerManager, reporting: Any, settings: Any) -> None:
        self.session_factory = session_factory
        self.runners = runners
        self.reporting = reporting
        self.settings = settings
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.last_tick: datetime | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        self.scheduler.add_job(self._tick, "interval", seconds=1, id="scheduled-test-tick", max_instances=1, coalesce=True)
        self.scheduler.add_job(self._cleanup, "cron", hour=3, minute=15, id="retention-cleanup", max_instances=1, coalesce=True)
        self.scheduler.start()
        LOGGER.info("scheduler_started")

    async def _tick(self) -> None:
        self.last_tick = datetime.now(timezone.utc)
        try:
            with self.session_factory() as session:
                rows = session.scalars(select(Test).where(Test.status == "scheduled", Test.start_at.is_not(None))).all()
                due = [row.id for row in rows if aware(row.start_at) and aware(row.start_at) <= self.last_tick]
            for test_id in due:
                try:
                    await self.runners.start(test_id)
                except ValueError:
                    continue
            self.last_error = None
        except Exception as exc:
            self.last_error = type(exc).__name__
            LOGGER.exception("scheduler_tick_failed")

    async def _cleanup(self) -> None:
        removed = self.reporting.cleanup(int(self.settings.get("retention_days", 90)))
        LOGGER.info("retention_cleanup_complete", extra={"removed_requests": removed})

    def status(self) -> dict[str, object]:
        return {
            "running": self.scheduler.running,
            "last_tick": self.last_tick,
            "last_error": self.last_error,
            "jobs": len(self.scheduler.get_jobs()) if self.scheduler.running else 0,
        }

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        LOGGER.info("scheduler_stopped")
