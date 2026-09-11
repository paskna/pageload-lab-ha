"""SQLite configuration and small, safe schema migration runner."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, SchemaVersion

LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
        )
        event.listen(self.engine, "connect", self._configure_connection)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)

    @staticmethod
    def _configure_connection(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.session() as session:
            row = session.get(SchemaVersion, 1)
            if row is None:
                session.add(SchemaVersion(id=1, version=SCHEMA_VERSION))
            elif row.version > SCHEMA_VERSION:
                raise RuntimeError(f"Database schema {row.version} is newer than supported {SCHEMA_VERSION}")
            elif row.version < SCHEMA_VERSION:
                self._migrate(session, row.version)
        self.check_integrity()

    def _migrate(self, session: Session, current: int) -> None:
        """Apply ordered future migrations in one transaction."""
        version = current
        migrations: dict[int, tuple[str, ...]] = {}
        while version < SCHEMA_VERSION:
            next_version = version + 1
            for statement in migrations.get(next_version, ()):
                session.execute(text(statement))
            version = next_version
        row = session.get(SchemaVersion, 1)
        if row:
            row.version = version
        LOGGER.info("database_migrated", extra={"from_version": current, "to_version": version})

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def check_integrity(self) -> str:
        with self.engine.connect() as connection:
            result = connection.execute(text("PRAGMA integrity_check")).scalar_one()
        if result != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {result}")
        return result

    def status(self) -> dict[str, object]:
        return {
            "ok": self.check_integrity() == "ok",
            "schema_version": SCHEMA_VERSION,
            "tables": inspect(self.engine).get_table_names(),
            "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }

    def close(self) -> None:
        self.engine.dispose()
