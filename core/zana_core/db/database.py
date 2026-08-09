"""Database lifecycle: migrations, pragma inspection, and session factory."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from zana_core.db.engine import create_database_engine, sqlite_url

CORE_ROOT = Path(__file__).resolve().parents[2]


class Database:
    """Owns the SQLite engine, Alembic migrations, and session factory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.engine: Engine = create_database_engine(path)
        self.session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

    def upgrade(self) -> str:
        """Run Alembic to the latest revision and return the revision id."""
        config = self._alembic_config()
        command.upgrade(config, "head")
        revision = self.revision()
        if revision is None:
            raise RuntimeError("Alembic finished without recording a revision.")
        return revision

    def downgrade_to_base(self) -> None:
        """Drop all schema objects via Alembic (used only by tests)."""
        command.downgrade(self._alembic_config(), "base")

    def revision(self) -> str | None:
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT version_num FROM alembic_version")).first()
        return str(row[0]) if row is not None else None

    def pragma_state(self) -> dict[str, str | int]:
        with self.engine.connect() as connection:
            journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
            foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
            busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
        return {
            "journal_mode": str(journal_mode),
            "foreign_keys": int(foreign_keys),
            "busy_timeout_ms": int(busy_timeout),
        }

    def table_counts(self) -> dict[str, int]:
        tables = (
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
        )
        counts: dict[str, int] = {}
        with self.engine.connect() as connection:
            for table in tables:
                row = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).first()
                counts[table] = int(row[0]) if row is not None else -1
        return counts

    def close(self) -> None:
        self.engine.dispose()

    def _alembic_config(self) -> Config:
        config = Config(str(CORE_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(CORE_ROOT / "migrations"))
        config.set_main_option("sqlalchemy.url", sqlite_url(self.path).replace("%", "%%"))
        return config
