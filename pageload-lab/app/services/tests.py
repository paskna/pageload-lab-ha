"""Test configuration lifecycle and global safety validation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ..models import Proxy, RequestRecord, Test
from ..schemas import TestInput
from ..security import validate_target_url


TEST_FIELDS = {
    "name", "url", "mode", "start_type", "start_at", "timezone",
    "frequency_per_minute", "frequency_mode", "stay_mode", "stay_min_seconds",
    "stay_max_seconds", "duration_mode", "duration_days", "duration_hours",
    "duration_minutes", "end_at", "max_requests", "max_concurrency", "proxy_mode",
    "proxy_id", "reporting_enabled", "abort_error_rate_percent",
    "abort_consecutive_errors", "abort_load_seconds", "abort_timeouts",
    "abort_on_429", "abort_on_503",
}


class TestService:
    def __init__(self, session_factory: Any, settings: Any) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def validate(self, payload: TestInput) -> list[str]:
        config = self.settings.all()
        await validate_target_url(payload.url, bool(config["allow_private_targets"]))
        if payload.frequency_per_minute > int(config["max_requests_per_minute"]):
            raise ValueError(f"Die Frequenz überschreitet das globale Limit von {config['max_requests_per_minute']} Aufrufen pro Minute.")
        if payload.max_concurrency > int(config["max_parallel_browsers"]):
            raise ValueError(f"Die Parallelität überschreitet das globale Limit von {config['max_parallel_browsers']} Browser Sessions.")
        if payload.stay_max_seconds > int(config["max_stay_seconds"]):
            raise ValueError(f"Die Aufenthaltsdauer überschreitet das globale Limit von {config['max_stay_seconds']} Sekunden.")
        if payload.duration_mode == "time":
            hours = payload.duration_days * 24 + payload.duration_hours + payload.duration_minutes / 60
            if hours > int(config["max_test_duration_hours"]):
                raise ValueError(f"Die Testdauer überschreitet das globale Limit von {config['max_test_duration_hours']} Stunden.")
        if payload.duration_mode == "end_at" and payload.end_at:
            now = datetime.now(timezone.utc)
            end_at = _to_utc(payload.end_at, payload.timezone)
            if end_at <= now:
                raise ValueError("Das Enddatum muss in der Zukunft liegen.")
            if (end_at - now).total_seconds() > int(config["max_test_duration_hours"]) * 3600:
                raise ValueError(f"Das Enddatum überschreitet das globale Laufzeitlimit von {config['max_test_duration_hours']} Stunden.")
        if payload.start_type == "scheduled" and payload.start_at and _to_utc(payload.start_at, payload.timezone) <= datetime.now(timezone.utc):
            raise ValueError("Der geplante Start muss in der Zukunft liegen.")
        if payload.proxy_mode == "fixed":
            with self.session_factory() as session:
                proxy = session.get(Proxy, payload.proxy_id)
                if proxy is None or not proxy.enabled:
                    raise ValueError("Der gewählte feste Proxy ist nicht aktiv oder nicht vorhanden.")
        needed = estimated_concurrency(payload.frequency_per_minute, payload.stay_max_seconds)
        warnings = []
        if payload.mode == "browser" and needed > payload.max_concurrency:
            warnings.append(f"Mit den gewählten Einstellungen werden ungefähr {needed} parallele Browser Sessions benötigt. Das aktuelle Limit beträgt {payload.max_concurrency}.")
        return warnings

    async def create(self, payload: TestInput) -> tuple[Test, list[str]]:
        warnings = await self.validate(payload)
        values = {key: value for key, value in payload.model_dump().items() if key in TEST_FIELDS}
        values["name"] = payload.name.strip()
        values["status"] = "scheduled" if payload.start_type == "scheduled" else "ready"
        values["start_at"] = _to_utc(payload.start_at, payload.timezone) if payload.start_at else None
        values["end_at"] = _to_utc(payload.end_at, payload.timezone) if payload.end_at else None
        with self.session_factory() as session:
            row = Test(**values)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row, warnings

    async def update(self, test_id: int, payload: TestInput) -> tuple[Test, list[str]]:
        warnings = await self.validate(payload)
        with self.session_factory() as session:
            row = session.get(Test, test_id)
            if row is None:
                raise KeyError(test_id)
            if row.status in {"running", "paused", "waiting_capacity"}:
                raise ValueError("Ein laufender oder pausierter Test kann nicht bearbeitet werden.")
            for key, value in payload.model_dump().items():
                if key in TEST_FIELDS:
                    setattr(row, key, value)
            row.start_at = _to_utc(payload.start_at, payload.timezone) if payload.start_at else None
            row.end_at = _to_utc(payload.end_at, payload.timezone) if payload.end_at else None
            row.status = "scheduled" if payload.start_type == "scheduled" else "ready"
            session.commit()
            return row, warnings

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(select(Test).order_by(Test.created_at.desc()).limit(min(limit, 1000))).all()
            return [serialize_test(row) for row in rows]

    def get(self, test_id: int) -> Test:
        with self.session_factory() as session:
            row = session.get(Test, test_id)
            if row is None:
                raise KeyError(test_id)
            session.expunge(row)
            return row

    def duplicate(self, test_id: int) -> Test:
        source = self.get(test_id)
        values = {key: getattr(source, key) for key in TEST_FIELDS}
        values.update({
            "name": f"{source.name} – Kopie",
            "start_type": "immediate",
            "start_at": None,
            "status": "ready",
            "started_at": None,
            "finished_at": None,
        })
        with self.session_factory() as session:
            row = Test(**values)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def restart_copy(self, test_id: int) -> Test:
        row = self.duplicate(test_id)
        with self.session_factory() as session:
            saved = session.get(Test, row.id)
            assert saved
            saved.name = self.get(test_id).name
            session.commit()
            session.refresh(saved)
            return saved

    def delete(self, test_id: int) -> None:
        with self.session_factory() as session:
            row = session.get(Test, test_id)
            if row is None:
                raise KeyError(test_id)
            if row.status in {"running", "paused", "waiting_capacity"}:
                raise ValueError("Ein laufender oder pausierter Test kann nicht gelöscht werden.")
            session.delete(row)
            session.commit()


def estimated_concurrency(frequency_per_minute: int, stay_seconds: int) -> int:
    if stay_seconds <= 0:
        return 1
    return max(1, int((frequency_per_minute * stay_seconds + 59) // 60))


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_utc(value: datetime, zone_name: str) -> datetime:
    """Interpret browser datetime-local values in the explicitly selected zone."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(zone_name))
    return value.astimezone(timezone.utc)


def serialize_test(row: Test) -> dict[str, Any]:
    data = row.as_dict()
    total = row.total_requests
    data["success_rate"] = round(row.successful_requests * 100 / total, 2) if total else 0.0
    data["average_ms"] = round(row.total_load_time_ms / total, 2) if total else None
    data["runtime_seconds"] = (
        max(0, (_aware_utc(row.finished_at or datetime.now(timezone.utc)) - _aware_utc(row.started_at)).total_seconds())
        if row.started_at else 0
    )
    return data
