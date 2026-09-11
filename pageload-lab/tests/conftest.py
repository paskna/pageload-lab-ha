from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Database
from app.settings import SettingsStore


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "pageloadlab.db")
    db.initialize()
    yield db
    db.close()


@pytest.fixture
def settings(database: Database) -> SettingsStore:
    store = SettingsStore(database.session_factory)
    store.ensure_defaults()
    return store
