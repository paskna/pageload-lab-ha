from sqlalchemy import inspect, text

from app.models import SchemaVersion, Test


def test_database_initialization_and_integrity(database):
    assert database.check_integrity() == "ok"
    assert database.status()["schema_version"] == 1
    with database.session() as session:
        assert session.get(SchemaVersion, 1).version == 1
        session.add(Test(name="Persistenz", url="https://example.com", max_requests=1))
    with database.session() as session:
        assert session.query(Test).one().name == "Persistenz"


def test_required_indexes_exist(database):
    inspector = inspect(database.engine)
    request_indexes = {row["name"] for row in inspector.get_indexes("requests")}
    test_indexes = {row["name"] for row in inspector.get_indexes("tests")}
    assert {"ix_requests_test_started", "ix_requests_started_at", "ix_requests_success", "ix_requests_http_status"} <= request_indexes
    assert {"ix_tests_status", "ix_tests_started_at"} <= test_indexes
    with database.engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "wal"
