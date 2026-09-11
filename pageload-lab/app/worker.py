"""Concurrent test runners with exact request limits and graceful control."""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .browser import BrowserEngine, VisitResult
from .models import RequestRecord, Test
from .proxy_manager import ProxyManager
from .reporting import ReportingService

LOGGER = logging.getLogger(__name__)
ACTIVE_STATUSES = {"running", "paused", "waiting_capacity"}


def minute_offsets(frequency: int, mode: str, rng: random.Random | None = None) -> list[float]:
    if frequency < 1:
        raise ValueError("frequency must be positive")
    if mode == "even":
        return [index * 60.0 / frequency for index in range(frequency)]
    if mode == "random":
        generator = rng or random
        return sorted(generator.uniform(0, 60) for _ in range(frequency))
    raise ValueError("frequency mode must be even or random")


def duration_deadline(test: Test, started_at: datetime) -> datetime | None:
    if test.duration_mode == "time":
        return started_at + timedelta(days=test.duration_days, hours=test.duration_hours, minutes=test.duration_minutes)
    if test.duration_mode == "end_at":
        return aware(test.end_at) if test.end_at else None
    return None


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class Runtime:
    test_id: int
    semaphore: asyncio.Semaphore
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    resume_event: asyncio.Event = field(default_factory=asyncio.Event)
    active: set[asyncio.Task[None]] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    launched: int = 0
    shutting_down: bool = False
    abort_reason: str | None = None

    def __post_init__(self) -> None:
        self.resume_event.set()


class TestRunnerManager:
    __test__ = False
    def __init__(self, session_factory: Any, engine: BrowserEngine, proxies: ProxyManager, reporting: ReportingService) -> None:
        self.session_factory = session_factory
        self.engine = engine
        self.proxies = proxies
        self.reporting = reporting
        self.runtimes: dict[int, Runtime] = {}
        self._lock = asyncio.Lock()
        self.shutting_down = False

    async def start(self, test_id: int) -> None:
        async with self._lock:
            if self.shutting_down:
                raise RuntimeError("PageLoad Lab wird heruntergefahren; es werden keine neuen Tests gestartet.")
            current = self.runtimes.get(test_id)
            if current and current.task and not current.task.done():
                raise ValueError("Der Test läuft bereits.")
            with self.session_factory() as session:
                test = session.get(Test, test_id)
                if test is None:
                    raise KeyError(test_id)
                if test.status not in {"ready", "scheduled", "interrupted"}:
                    raise ValueError("Dieser Test kann in seinem aktuellen Status nicht gestartet werden.")
                test.status = "running"
                test.status_message = None
                test.started_at = datetime.now(timezone.utc)
                test.finished_at = None
                test.total_requests = test.successful_requests = test.failed_requests = 0
                test.timeout_count = test.consecutive_errors = 0
                test.total_load_time_ms = 0.0
                runtime = Runtime(test_id=test_id, semaphore=asyncio.Semaphore(test.max_concurrency))
                session.commit()
            # Start only after the transaction above committed. Otherwise an
            # immediate status poll can still observe the previous state.
            runtime.task = asyncio.create_task(self._run(runtime), name=f"pageload-test-{test_id}")
            self.runtimes[test_id] = runtime
        LOGGER.info("test_started", extra={"test_id": test_id})

    async def pause(self, test_id: int) -> None:
        runtime = self._runtime(test_id)
        runtime.resume_event.clear()
        self._set_status(test_id, "paused", "Pausiert; aktive Sessions werden abgeschlossen.")

    async def resume(self, test_id: int) -> None:
        runtime = self._runtime(test_id)
        runtime.resume_event.set()
        self._set_status(test_id, "running", None)

    async def stop(self, test_id: int) -> None:
        runtime = self._runtime(test_id)
        runtime.stop_event.set()
        runtime.resume_event.set()
        if runtime.task:
            await runtime.task

    def _runtime(self, test_id: int) -> Runtime:
        runtime = self.runtimes.get(test_id)
        if not runtime or not runtime.task or runtime.task.done():
            raise ValueError("Der Test läuft derzeit nicht.")
        return runtime

    async def _run(self, runtime: Runtime) -> None:
        final_status = "completed"
        try:
            with self.session_factory() as session:
                test = session.get(Test, runtime.test_id)
                assert test is not None
                started_at = aware(test.started_at) or datetime.now(timezone.utc)
                deadline = duration_deadline(test, started_at)
                config = test.as_dict()
            anchor = asyncio.get_running_loop().time()
            bucket = 0
            while not runtime.stop_event.is_set():
                if not runtime.resume_event.is_set():
                    await runtime.resume_event.wait()
                    anchor = asyncio.get_running_loop().time()
                    bucket = 0
                    if runtime.stop_event.is_set():
                        break
                offsets = minute_offsets(config["frequency_per_minute"], config["frequency_mode"])
                reset_after_pause = False
                for offset in offsets:
                    if runtime.stop_event.is_set() or self._termination_reached(config, runtime.launched, deadline):
                        break
                    due = anchor + bucket * 60 + offset
                    if not await self._wait_due(runtime, due):
                        reset_after_pause = not runtime.resume_event.is_set()
                        break
                    if runtime.stop_event.is_set() or self._termination_reached(config, runtime.launched, deadline):
                        break
                    runtime.launched += 1
                    task = asyncio.create_task(self._run_one(runtime, config), name=f"pageload-visit-{runtime.test_id}-{runtime.launched}")
                    runtime.active.add(task)
                    task.add_done_callback(runtime.active.discard)
                if reset_after_pause:
                    continue
                if runtime.stop_event.is_set() or self._termination_reached(config, runtime.launched, deadline):
                    break
                bucket += 1
            if runtime.active:
                await asyncio.gather(*list(runtime.active), return_exceptions=True)
            if runtime.shutting_down:
                final_status = "interrupted"
            elif runtime.abort_reason:
                final_status = "stopped"
            elif runtime.stop_event.is_set():
                final_status = "stopped"
        except Exception:
            LOGGER.exception("test_runner_failed", extra={"test_id": runtime.test_id})
            final_status = "error"
        finally:
            self._finish(runtime.test_id, final_status, runtime.abort_reason)
            try:
                self.reporting.create_snapshot(runtime.test_id)
            except Exception:
                LOGGER.exception("report_snapshot_failed", extra={"test_id": runtime.test_id})

    async def _wait_due(self, runtime: Runtime, due: float) -> bool:
        while True:
            if runtime.stop_event.is_set() or not runtime.resume_event.is_set():
                return False
            remaining = due - asyncio.get_running_loop().time()
            if remaining <= 0:
                return True
            try:
                await asyncio.wait_for(runtime.stop_event.wait(), timeout=min(remaining, 0.5))
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _termination_reached(config: dict[str, Any], launched: int, deadline: datetime | None) -> bool:
        if config["duration_mode"] == "requests" and launched >= int(config["max_requests"] or 0):
            return True
        return bool(deadline and datetime.now(timezone.utc) >= deadline)

    async def _run_one(self, runtime: Runtime, config: dict[str, Any]) -> None:
        async with runtime.semaphore:
            if runtime.stop_event.is_set() and runtime.shutting_down:
                return
            stay = self._stay(config)
            session_id = str(uuid.uuid4())
            proxy = None
            try:
                proxy = await self.proxies.select(config["proxy_mode"], config.get("proxy_id"))
                result = await self.engine.visit(
                    config["url"], config["mode"], stay, proxy,
                    lambda waiting: self._capacity(runtime.test_id, waiting),
                )
            except Exception as exc:
                LOGGER.warning("visit_setup_failed", extra={"test_id": runtime.test_id, "error": type(exc).__name__})
                now = datetime.now(timezone.utc)
                result = VisitResult(now, now, None, False, error_type="proxy" if config["proxy_mode"] != "direct" else "validation", error_message=str(exc)[:500])
            abort_reason = self._record(runtime.test_id, session_id, config, proxy, result)
            if abort_reason and not runtime.abort_reason:
                runtime.abort_reason = abort_reason
                runtime.stop_event.set()
                runtime.resume_event.set()

    @staticmethod
    def _stay(config: dict[str, Any]) -> float:
        if config["mode"] == "http" or config["stay_mode"] == "none":
            return 0.0
        if config["stay_mode"] == "fixed":
            return float(config["stay_min_seconds"])
        return random.uniform(float(config["stay_min_seconds"]), float(config["stay_max_seconds"]))

    async def _capacity(self, test_id: int, waiting: bool) -> None:
        with self.session_factory() as session:
            test = session.get(Test, test_id)
            if not test or test.status == "paused":
                return
            test.status = "waiting_capacity" if waiting else "running"
            test.status_message = "Wartet auf freie Browser Kapazität" if waiting else None
            session.commit()

    def _record(self, test_id: int, session_id: str, config: dict[str, Any], proxy: Any, result: VisitResult) -> str | None:
        with self.session_factory() as session:
            test = session.get(Test, test_id)
            assert test is not None
            if config["reporting_enabled"]:
                session.add(RequestRecord(
                    test_id=test_id,
                    session_id=session_id,
                    url=config["url"],
                    connection_label=proxy.name if proxy else "Direkt",
                    started_at=result.started_at,
                    finished_at=result.finished_at,
                    http_status=result.http_status,
                    success=result.success,
                    navigation_time_ms=result.navigation_time_ms,
                    load_time_ms=result.load_time_ms,
                    ttfb_ms=result.ttfb_ms,
                    dom_content_loaded_ms=result.dom_content_loaded_ms,
                    load_event_ms=result.load_event_ms,
                    stay_time_seconds=result.stay_time_seconds,
                    proxy_id=proxy.id if proxy else None,
                    error_type=result.error_type,
                    error_message=result.error_message,
                ))
            test.total_requests += 1
            if result.success:
                test.successful_requests += 1
                test.consecutive_errors = 0
            else:
                test.failed_requests += 1
                test.consecutive_errors += 1
            if result.error_type == "timeout":
                test.timeout_count += 1
            if result.load_time_ms is not None:
                test.total_load_time_ms += result.load_time_ms
            reason = self._abort_reason(test, result)
            session.commit()
            return reason

    @staticmethod
    def _abort_reason(test: Test, result: VisitResult) -> str | None:
        if test.abort_error_rate_percent is not None and test.total_requests and test.failed_requests * 100 / test.total_requests > test.abort_error_rate_percent:
            return f"Abbruchbedingung erreicht: Fehlerquote über {test.abort_error_rate_percent:g} %."
        if test.abort_consecutive_errors is not None and test.consecutive_errors >= test.abort_consecutive_errors:
            return f"Abbruchbedingung erreicht: {test.abort_consecutive_errors} aufeinanderfolgende Fehler."
        if test.abort_load_seconds is not None and result.load_time_ms is not None and result.load_time_ms > test.abort_load_seconds * 1000:
            return f"Abbruchbedingung erreicht: Ladezeit länger als {test.abort_load_seconds:g} Sekunden."
        if test.abort_timeouts is not None and test.timeout_count >= test.abort_timeouts:
            return f"Abbruchbedingung erreicht: {test.abort_timeouts} Timeouts."
        if test.abort_on_429 and result.http_status == 429:
            return "Abbruchbedingung erreicht: HTTP 429."
        if test.abort_on_503 and result.http_status == 503:
            return "Abbruchbedingung erreicht: HTTP 503."
        return None

    def _set_status(self, test_id: int, status: str, message: str | None) -> None:
        with self.session_factory() as session:
            test = session.get(Test, test_id)
            if test is None:
                raise KeyError(test_id)
            test.status = status
            test.status_message = message
            session.commit()

    def _finish(self, test_id: int, status: str, message: str | None) -> None:
        with self.session_factory() as session:
            test = session.get(Test, test_id)
            if test:
                test.status = status
                test.status_message = message
                test.finished_at = datetime.now(timezone.utc)
                session.commit()
        LOGGER.info("test_finished", extra={"test_id": test_id, "status": status})

    async def shutdown(self) -> None:
        self.shutting_down = True
        tasks = []
        for runtime in self.runtimes.values():
            if runtime.task and not runtime.task.done():
                runtime.shutting_down = True
                runtime.stop_event.set()
                runtime.resume_event.set()
                tasks.append(runtime.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def active_sessions(self, test_id: int | None = None) -> int:
        if test_id is None:
            return self.engine.active_sessions
        runtime = self.runtimes.get(test_id)
        return len(runtime.active) if runtime else 0
