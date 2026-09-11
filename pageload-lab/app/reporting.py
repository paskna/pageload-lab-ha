"""Efficient statistics, snapshots, retention, pagination, and exports."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, delete, func, select

from .models import ReportSnapshot, RequestRecord, Test


PERCENTILES = (0.50, 0.90, 0.95, 0.99)


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


class ReportingService:
    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory

    def stats(self, test_id: int) -> dict[str, Any]:
        with self.session_factory() as session:
            test = session.get(Test, test_id)
            if test is None:
                raise KeyError(test_id)
            snapshot = session.scalar(
                select(ReportSnapshot)
                .where(ReportSnapshot.test_id == test_id)
                .order_by(ReportSnapshot.created_at.desc())
                .limit(1)
            )
            if snapshot and test.status in {"completed", "stopped", "error", "interrupted"}:
                saved = json.loads(snapshot.summary_json)
                saved["status"] = test.status
                saved["status_message"] = test.status_message
                return saved
            aggregate = session.execute(
                select(
                    func.count(RequestRecord.id),
                    func.sum(case((RequestRecord.success.is_(True), 1), else_=0)),
                    func.min(RequestRecord.load_time_ms),
                    func.max(RequestRecord.load_time_ms),
                    func.avg(RequestRecord.load_time_ms),
                ).where(RequestRecord.test_id == test_id)
            ).one()
            count = int(aggregate[0] or 0)
            successes = int(aggregate[1] or 0)
            failures = count - successes
            if count == 0 and test.total_requests:
                total = test.total_requests
                return {
                    "test_id": test_id,
                    "status": test.status,
                    "status_message": test.status_message,
                    "total": total,
                    "successful": test.successful_requests,
                    "failed": test.failed_requests,
                    "success_rate": round(test.successful_requests * 100 / total, 2),
                    "error_rate": round(test.failed_requests * 100 / total, 2),
                    "average_ms": round(test.total_load_time_ms / total, 2),
                    "minimum_ms": None,
                    "maximum_ms": None,
                    "median_ms": None,
                    "p50_ms": None,
                    "p90_ms": None,
                    "p95_ms": None,
                    "p99_ms": None,
                    "http_status_codes": {},
                    "errors": {},
                    "timeouts": test.timeout_count,
                }
            quantiles = self._quantiles(session, test_id, count)
            status_rows = session.execute(
                select(RequestRecord.http_status, func.count(RequestRecord.id))
                .where(RequestRecord.test_id == test_id)
                .group_by(RequestRecord.http_status)
            ).all()
            errors = session.execute(
                select(RequestRecord.error_type, func.count(RequestRecord.id))
                .where(RequestRecord.test_id == test_id, RequestRecord.error_type.is_not(None))
                .group_by(RequestRecord.error_type)
            ).all()
            return {
                "test_id": test_id,
                "status": test.status,
                "status_message": test.status_message,
                "total": count,
                "successful": successes,
                "failed": failures,
                "success_rate": round(successes * 100 / count, 2) if count else 0.0,
                "error_rate": round(failures * 100 / count, 2) if count else 0.0,
                "average_ms": round(float(aggregate[4]), 2) if aggregate[4] is not None else None,
                "minimum_ms": round(float(aggregate[2]), 2) if aggregate[2] is not None else None,
                "maximum_ms": round(float(aggregate[3]), 2) if aggregate[3] is not None else None,
                "median_ms": quantiles["p50_ms"],
                **quantiles,
                "http_status_codes": {str(status or "none"): number for status, number in status_rows},
                "errors": {str(kind): number for kind, number in errors},
                "timeouts": int(dict(errors).get("timeout", 0)),
            }

    @staticmethod
    def _quantiles(session: Any, test_id: int, total_count: int) -> dict[str, float | None]:
        valid_count = session.scalar(
            select(func.count(RequestRecord.id)).where(
                RequestRecord.test_id == test_id, RequestRecord.load_time_ms.is_not(None)
            )
        ) or 0
        result: dict[str, float | None] = {}
        for q in PERCENTILES:
            key = f"p{int(q * 100)}_ms"
            if not valid_count:
                result[key] = None
                continue
            offset = round((valid_count - 1) * q)
            value = session.scalar(
                select(RequestRecord.load_time_ms)
                .where(RequestRecord.test_id == test_id, RequestRecord.load_time_ms.is_not(None))
                .order_by(RequestRecord.load_time_ms)
                .offset(offset)
                .limit(1)
            )
            result[key] = round(float(value), 2) if value is not None else None
        return result

    def series(self, test_id: int, limit: int = 240) -> dict[str, list[dict[str, Any]]]:
        with self.session_factory() as session:
            test = session.get(Test, test_id)
            if test is None:
                raise KeyError(test_id)
            snapshot = session.scalar(
                select(ReportSnapshot)
                .where(ReportSnapshot.test_id == test_id)
                .order_by(ReportSnapshot.created_at.desc())
                .limit(1)
            )
            if snapshot and test.status in {"completed", "stopped", "error", "interrupted"}:
                saved = json.loads(snapshot.series_json)
                saved["timeline"] = saved.get("timeline", [])[-limit:]
                return saved
            minute = func.strftime("%Y-%m-%dT%H:%M:00Z", RequestRecord.started_at)
            rows = session.execute(
                select(
                    minute.label("minute"),
                    func.count(RequestRecord.id),
                    func.avg(RequestRecord.load_time_ms),
                    func.sum(case((RequestRecord.success.is_(True), 1), else_=0)),
                )
                .where(RequestRecord.test_id == test_id)
                .group_by(minute)
                .order_by(minute.desc())
                .limit(limit)
            ).all()
            points = [
                {"time": row[0], "requests": row[1], "average_ms": round(row[2] or 0, 2), "successful": row[3] or 0, "failed": row[1] - (row[3] or 0)}
                for row in reversed(rows)
            ]
            return {"timeline": points}

    def requests(self, test_id: int, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        with self.session_factory() as session:
            total = session.scalar(select(func.count(RequestRecord.id)).where(RequestRecord.test_id == test_id)) or 0
            rows = session.scalars(
                select(RequestRecord)
                .where(RequestRecord.test_id == test_id)
                .order_by(RequestRecord.started_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            items = [{column.name: getattr(row, column.name) for column in row.__table__.columns} for row in rows]
            return {"items": items, "page": page, "page_size": page_size, "total": total, "pages": max(1, (total + page_size - 1) // page_size)}

    def create_snapshot(self, test_id: int) -> ReportSnapshot:
        summary = self.stats(test_id)
        series = self.series(test_id, limit=10000)
        with self.session_factory() as session:
            row = ReportSnapshot(test_id=test_id, summary_json=json.dumps(summary), series_json=json.dumps(series))
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def cleanup(self, retention_days: int) -> int:
        if retention_days == 0:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        with self.session_factory() as session:
            result = session.execute(delete(RequestRecord).where(RequestRecord.started_at < cutoff))
            return int(result.rowcount or 0)

    def export(self, test_id: int, format_name: str) -> tuple[bytes, str, str]:
        with self.session_factory() as session:
            test = session.get(Test, test_id)
            if test is None:
                raise KeyError(test_id)
            rows = session.scalars(select(RequestRecord).where(RequestRecord.test_id == test_id).order_by(RequestRecord.started_at)).all()
            values = [{column.name: getattr(row, column.name) for column in row.__table__.columns} for row in rows]
        if format_name == "json":
            payload = json.dumps({"test": test.as_dict(), "statistics": self.stats(test_id), "requests": values}, default=str, indent=2).encode()
            return payload, "application/json", f"pageload-lab-test-{test_id}.json"
        if format_name != "csv":
            raise ValueError("Exportformat muss csv oder json sein.")
        output = io.StringIO()
        fieldnames = list(values[0]) if values else [column.name for column in RequestRecord.__table__.columns]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(values)
        return output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", f"pageload-lab-test-{test_id}.csv"
