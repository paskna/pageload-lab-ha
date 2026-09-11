"""Pydantic API contracts and validation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=8, max_length=2048)
    mode: Literal["browser", "http"] = "browser"
    start_type: Literal["immediate", "scheduled"] = "immediate"
    start_at: datetime | None = None
    timezone: str = "Europe/Zurich"
    frequency_per_minute: int = Field(default=1, ge=1)
    frequency_mode: Literal["even", "random"] = "even"
    stay_mode: Literal["none", "fixed", "random"] = "none"
    stay_min_seconds: int = Field(default=0, ge=0)
    stay_max_seconds: int = Field(default=0, ge=0)
    duration_mode: Literal["time", "end_at", "requests"] = "requests"
    duration_days: int = Field(default=0, ge=0)
    duration_hours: int = Field(default=0, ge=0)
    duration_minutes: int = Field(default=0, ge=0)
    end_at: datetime | None = None
    max_requests: int | None = Field(default=1, ge=1)
    max_concurrency: int = Field(default=2, ge=1)
    proxy_mode: Literal["direct", "random", "round_robin", "fixed"] = "direct"
    proxy_id: int | None = None
    reporting_enabled: bool = True
    authorization_confirmed: bool = False
    abort_error_rate_percent: float | None = Field(default=None, gt=0, le=100)
    abort_consecutive_errors: int | None = Field(default=None, ge=1)
    abort_load_seconds: float | None = Field(default=None, gt=0)
    abort_timeouts: int | None = Field(default=None, ge=1)
    abort_on_429: bool = False
    abort_on_503: bool = False

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Die Zeitzone ist nicht gültig.") from exc
        return value

    @model_validator(mode="after")
    def validate_combinations(self) -> "TestInput":
        if not self.authorization_confirmed:
            raise ValueError("Die ausdrückliche Testberechtigung muss bestätigt werden.")
        if self.start_type == "scheduled" and self.start_at is None:
            raise ValueError("Für einen geplanten Start ist ein Datum mit Uhrzeit erforderlich.")
        if self.stay_mode == "fixed":
            if self.stay_min_seconds < 1:
                raise ValueError("Die fixe Aufenthaltsdauer muss mindestens eine Sekunde betragen.")
            self.stay_max_seconds = self.stay_min_seconds
        elif self.stay_mode == "random":
            if self.stay_min_seconds < 1 or self.stay_max_seconds < self.stay_min_seconds:
                raise ValueError("Der zufällige Aufenthaltsbereich ist ungültig.")
        else:
            self.stay_min_seconds = self.stay_max_seconds = 0
        if self.mode == "http":
            self.stay_mode = "none"
            self.stay_min_seconds = self.stay_max_seconds = 0
        if self.duration_mode == "time":
            if self.duration_days + self.duration_hours + self.duration_minutes < 1:
                raise ValueError("Die Testdauer muss mindestens eine Minute betragen.")
            self.end_at = None
            self.max_requests = None
        elif self.duration_mode == "end_at":
            if self.end_at is None:
                raise ValueError("Für den Enddatum-Modus ist ein Endzeitpunkt erforderlich.")
            self.max_requests = None
        else:
            if self.max_requests is None or self.max_requests < 1:
                raise ValueError("Die Anzahl Aufrufe muss mindestens 1 sein.")
            self.end_at = None
        if self.proxy_mode == "fixed" and self.proxy_id is None:
            raise ValueError("Für einen festen Proxy muss ein Proxy gewählt werden.")
        if self.proxy_mode != "fixed":
            self.proxy_id = None
        return self


class ProxyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    protocol: Literal["HTTP", "HTTPS", "SOCKS5"]
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=1024)
    clear_password: bool = False
    enabled: bool = True


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str | None = None
    max_requests_per_minute: int | None = Field(default=None, ge=1, le=600)
    max_parallel_browsers: int | None = Field(default=None, ge=1, le=50)
    max_stay_seconds: int | None = Field(default=None, ge=1, le=86400)
    max_test_duration_hours: int | None = Field(default=None, ge=1, le=8760)
    max_memory_percent: int | None = Field(default=None, ge=50, le=98)
    navigation_timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    retention_days: Literal[0, 7, 30, 90, 180, 365] | None = None
    allow_private_targets: bool | None = None
    proxy_test_url: str | None = Field(default=None, max_length=2048)
    onboarding_complete: bool | None = None
    authorization_confirmed: bool | None = None

    def supplied(self) -> dict[str, object]:
        return self.model_dump(exclude_none=True)
