from datetime import datetime, timedelta, timezone

import pytest

from app.models import RequestRecord, Test
from app.reporting import ReportingService, percentile


def seed(database):
    now = datetime.now(timezone.utc)
    with database.session() as session:
        test = Test(name="Report", url="https://example.com", status="completed", max_requests=5)
        session.add(test); session.flush()
        for index, value in enumerate([100, 200, 300, 400, 500]):
            session.add(RequestRecord(test_id=test.id, session_id=str(index), url=test.url, started_at=now+timedelta(seconds=index), finished_at=now+timedelta(seconds=index+1), http_status=200 if index<4 else 500, success=index<4, load_time_ms=value, stay_time_seconds=0))
        return test.id


def test_percentile_interpolation():
    assert percentile([1, 2, 3, 4, 5], .5) == 3
    assert percentile([1, 2, 3, 4, 5], .95) == pytest.approx(4.8)
    assert percentile([], .99) is None


def test_statistics_pagination_and_export(database):
    test_id = seed(database); service = ReportingService(database.session_factory); stats = service.stats(test_id)
    assert stats["total"] == 5 and stats["successful"] == 4 and stats["success_rate"] == 80
    assert stats["median_ms"] == 300 and stats["p95_ms"] == 500 and stats["p99_ms"] == 500
    assert stats["http_status_codes"] == {"200": 4, "500": 1}
    assert service.requests(test_id, page=2, page_size=2)["items"]
    csv_data, content_type, _ = service.export(test_id, "csv")
    assert b"session_id" in csv_data and "text/csv" in content_type
    json_data, _, _ = service.export(test_id, "json")
    assert b'"requests"' in json_data
    assert service.create_snapshot(test_id).test_id == test_id


def test_retention_keeps_snapshots(database):
    test_id = seed(database); service = ReportingService(database.session_factory); service.create_snapshot(test_id)
    with database.session() as session:
        session.query(RequestRecord).update({RequestRecord.started_at: datetime.now(timezone.utc)-timedelta(days=400)})
    assert service.cleanup(90) == 5
    with database.session() as session:
        assert len(session.get(Test, test_id).summaries) == 1
    assert service.stats(test_id)["total"] == 5
    assert service.series(test_id)["timeline"]
