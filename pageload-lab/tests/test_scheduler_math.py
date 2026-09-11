import random
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Test
from app.services.tests import _to_utc, estimated_concurrency, serialize_test
from app.worker import duration_deadline, minute_offsets


def test_even_frequency_offsets():
    assert minute_offsets(5, "even") == [0, 12, 24, 36, 48]


def test_random_frequency_is_exact_per_minute_and_sorted():
    offsets = minute_offsets(5, "random", random.Random(42))
    assert len(offsets) == 5 and offsets == sorted(offsets)
    assert all(0 <= item < 60 for item in offsets)


def test_invalid_frequency():
    with pytest.raises(ValueError):
        minute_offsets(0, "even")


def test_duration_calculation_and_concurrency():
    start = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
    test = Test(name="x", url="https://example.com", duration_mode="time", duration_days=2, duration_hours=4, duration_minutes=7)
    assert duration_deadline(test, start) == start + timedelta(days=2, hours=4, minutes=7)
    assert estimated_concurrency(10, 60) == 10
    assert estimated_concurrency(20, 60) == 20
    assert estimated_concurrency(5, 0) == 1


def test_completed_sqlite_naive_datetimes_are_serialized_safely():
    test = Test(
        name="x", url="https://example.com", status="completed",
        started_at=datetime(2026, 9, 8, 10),
        finished_at=datetime(2026, 9, 8, 10, 2),
    )
    assert serialize_test(test)["runtime_seconds"] == 120


def test_local_datetime_uses_explicit_timezone():
    local = datetime(2026, 9, 11, 18, 0)
    assert _to_utc(local, "Europe/Zurich") == datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc)
