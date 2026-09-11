"""Application paths and typed global settings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from .models import Setting


DEFAULTS: dict[str, Any] = {
    "onboarding_complete": False,
    "authorization_confirmed": False,
    "timezone": "Europe/Zurich",
    "max_requests_per_minute": 30,
    "max_parallel_browsers": 5,
    "max_stay_seconds": 300,
    "max_test_duration_hours": 168,
    "max_memory_percent": 85,
    "navigation_timeout_seconds": 30,
    "retention_days": 90,
    "allow_private_targets": False,
    "proxy_test_url": "https://example.com/",
}


@dataclass(frozen=True)
class Paths:
    data: Path
    database: Path
    reports: Path
    logs: Path
    secret_key: Path
    csrf_secret: Path

    @classmethod
    def from_environment(cls) -> "Paths":
        data = Path(os.environ.get("PAGELAB_DATA_DIR", "/data")).resolve()
        return cls(
            data=data,
            database=data / "pageloadlab.db",
            reports=data / "reports",
            logs=data / "logs",
            secret_key=data / "secret.key",
            csrf_secret=data / "csrf.secret",
        )

    def ensure(self) -> None:
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.reports.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.logs.mkdir(parents=True, exist_ok=True, mode=0o700)


class SettingsStore:
    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory

    def ensure_defaults(self) -> None:
        with self.session_factory() as session:
            for key, value in DEFAULTS.items():
                if session.get(Setting, key) is None:
                    session.add(Setting(key=key, value=json.dumps(value)))
            session.commit()

    def all(self) -> dict[str, Any]:
        result = dict(DEFAULTS)
        with self.session_factory() as session:
            for row in session.query(Setting).all():
                try:
                    result[row.key] = json.loads(row.value)
                except json.JSONDecodeError:
                    result[row.key] = row.value
        return result

    def get(self, key: str, default: Any = None) -> Any:
        with self.session_factory() as session:
            row = session.get(Setting, key)
            if row is None:
                return DEFAULTS.get(key, default)
            try:
                return json.loads(row.value)
            except json.JSONDecodeError:
                return row.value

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
        self._validate(values)
        with self.session_factory() as session:
            for key, value in values.items():
                row = session.get(Setting, key)
                encoded = json.dumps(value)
                if row is None:
                    session.add(Setting(key=key, value=encoded))
                else:
                    row.value = encoded
            session.commit()
        return self.all()

    @staticmethod
    def _validate(values: dict[str, Any]) -> None:
        positive = {
            "max_requests_per_minute",
            "max_parallel_browsers",
            "max_stay_seconds",
            "max_test_duration_hours",
            "navigation_timeout_seconds",
        }
        for key in positive & values.keys():
            if not isinstance(values[key], int) or values[key] < 1:
                raise ValueError(f"{key} must be a positive integer")
        if "max_memory_percent" in values and not 50 <= int(values["max_memory_percent"]) <= 98:
            raise ValueError("max_memory_percent must be between 50 and 98")
        if "retention_days" in values and values["retention_days"] not in (7, 30, 90, 180, 365, 0):
            raise ValueError("retention_days must be 7, 30, 90, 180, 365, or 0")
        if "timezone" in values:
            try:
                ZoneInfo(str(values["timezone"]))
            except ZoneInfoNotFoundError as exc:
                raise ValueError("Die Zeitzone ist nicht gültig.") from exc
