"""SQLAlchemy persistence models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Test(Base):
    __tablename__ = "tests"
    __test__ = False

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), default="browser", nullable=False)
    start_type: Mapped[str] = mapped_column(String(20), default="immediate", nullable=False)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Zurich", nullable=False)
    frequency_per_minute: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    frequency_mode: Mapped[str] = mapped_column(String(20), default="even", nullable=False)
    stay_mode: Mapped[str] = mapped_column(String(20), default="none", nullable=False)
    stay_min_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stay_max_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_mode: Mapped[str] = mapped_column(String(20), default="requests", nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_hours: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_requests: Mapped[int | None] = mapped_column(Integer)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    proxy_mode: Mapped[str] = mapped_column(String(20), default="direct", nullable=False)
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxies.id", ondelete="SET NULL"))
    reporting_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ready", nullable=False)
    status_message: Mapped[str | None] = mapped_column(String(500))

    abort_error_rate_percent: Mapped[float | None] = mapped_column(Float)
    abort_consecutive_errors: Mapped[int | None] = mapped_column(Integer)
    abort_load_seconds: Mapped[float | None] = mapped_column(Float)
    abort_timeouts: Mapped[int | None] = mapped_column(Integer)
    abort_on_429: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    abort_on_503: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    total_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successful_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    timeout_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_load_time_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    proxy: Mapped[Proxy | None] = relationship(foreign_keys=[proxy_id])
    requests: Mapped[list[RequestRecord]] = relationship(back_populates="test", cascade="all, delete-orphan")
    summaries: Mapped[list[ReportSnapshot]] = relationship(back_populates="test", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_tests_status", "status"),
        Index("ix_tests_started_at", "started_at"),
    )

    def as_dict(self) -> dict[str, Any]:
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class RequestRecord(Base):
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    connection_label: Mapped[str] = mapped_column(String(200), default="Direkt", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    navigation_time_ms: Mapped[float | None] = mapped_column(Float)
    load_time_ms: Mapped[float | None] = mapped_column(Float)
    ttfb_ms: Mapped[float | None] = mapped_column(Float)
    dom_content_loaded_ms: Mapped[float | None] = mapped_column(Float)
    load_event_ms: Mapped[float | None] = mapped_column(Float)
    stay_time_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxies.id", ondelete="SET NULL"))
    error_type: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1000))

    test: Mapped[Test] = relationship(back_populates="requests")
    proxy: Mapped[Proxy | None] = relationship(foreign_keys=[proxy_id])

    __table_args__ = (
        Index("ix_requests_test_started", "test_id", "started_at"),
        Index("ix_requests_started_at", "started_at"),
        Index("ix_requests_success", "success"),
        Index("ix_requests_http_status", "http_status"),
    )


class Proxy(Base):
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    protocol: Mapped[str] = mapped_column(String(10), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    encrypted_password: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class SchemaVersion(Base):
    __tablename__ = "schema_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ReportSnapshot(Base):
    __tablename__ = "report_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    series_json: Mapped[str] = mapped_column(Text, nullable=False)

    test: Mapped[Test] = relationship(back_populates="summaries")
    __table_args__ = (Index("ix_report_snapshots_test", "test_id", "created_at"),)
