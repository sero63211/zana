"""Fresh-database migration and SQLite pragma verification."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

from zana_core.db.database import Database

EXPECTED_TABLES = {
    "runtimes",
    "models",
    "capabilities",
    "capability_sources",
    "jobs",
    "job_events",
    "build_jobs",
    "artifacts",
    "images",
    "image_artifacts",
    "instances",
    "conversations",
    "messages",
    "memories",
    "state_snapshots",
}


class TestMigrations:
    def test_fresh_upgrade_creates_expected_tables(self, db_path: Path) -> None:
        db = Database(db_path)
        revision = db.upgrade()
        assert revision == "0001_initial_schema"
        tables = set(inspect(db.engine).get_table_names())
        assert tables >= EXPECTED_TABLES
        assert "alembic_version" in tables
        db.close()

    def test_required_sqlite_pragmas_are_active(self, db_path: Path) -> None:
        db = Database(db_path)
        db.upgrade()
        state = db.pragma_state()
        assert state["journal_mode"] == "wal"
        assert state["foreign_keys"] == 1
        assert state["busy_timeout_ms"] == 30000
        db.close()

    def test_downgrade_to_base_drops_schema(self, db_path: Path) -> None:
        db = Database(db_path)
        db.upgrade()
        db.downgrade_to_base()
        tables = set(inspect(db.engine).get_table_names())
        assert not EXPECTED_TABLES & tables
        db.close()
