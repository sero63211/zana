"""SQLite engine construction with the required pragmas."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import URL, create_engine, event
from sqlalchemy.engine import Engine


def sqlite_url(path: Path) -> str:
    """Build a SQLAlchemy URL for an absolute SQLite file path."""
    return str(URL.create(drivername="sqlite", database=str(path)))


def set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Enable WAL, foreign keys, and a busy timeout on every connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def create_database_engine(path: Path) -> Engine:
    """Create an engine for the ZANA SQLite database at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        sqlite_url(path),
        connect_args={"timeout": 30},
        pool_pre_ping=True,
    )
    event.listen(engine, "connect", set_sqlite_pragmas)
    return engine
