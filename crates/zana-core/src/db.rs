//! SQLite bootstrap and open behavior for the Rust Core.

use std::path::PathBuf;
use std::time::Duration;

use rusqlite::Connection;

use crate::error::CoreError;

/// Idempotent operational schema compatible with the accepted Python
/// migration. Existing Python-created tables are reused untouched; missing
/// Rust-owned operational tables are created. No `alembic_version` row is
/// ever claimed by the Rust bootstrap.
const OPERATIONAL_SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS runtimes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS models (
    key TEXT PRIMARY KEY,
    runtime_id INTEGER NOT NULL REFERENCES runtimes(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    digest TEXT,
    family TEXT,
    format TEXT,
    quantization TEXT,
    parameter_count INTEGER,
    size_bytes INTEGER,
    context_length INTEGER,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    identity_strength TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    last_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_models_runtime_id ON models(runtime_id);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    progress_0_1 REAL NOT NULL DEFAULT 0,
    phase TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    error_json TEXT
);
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    progress_0_1 REAL NOT NULL DEFAULT 0,
    error_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_job_events_job_id ON job_events(job_id);
CREATE INDEX IF NOT EXISTS ix_job_events_created_at ON job_events(created_at);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    sensitive INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resource_snapshots (
    revision INTEGER PRIMARY KEY,
    captured_at TEXT NOT NULL,
    platform TEXT NOT NULL,
    os_name TEXT NOT NULL DEFAULT '',
    arch TEXT NOT NULL DEFAULT '',
    logical_cores INTEGER,
    memory_total_bytes INTEGER,
    memory_available_bytes INTEGER,
    disk_free_bytes INTEGER,
    probe_error_code TEXT,
    probe_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    line TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    received_at TEXT NOT NULL
);
"#;

pub const OPERATIONAL_REVISION: &str = "zana-rust-operational-v1";

pub struct Database {
    pub path: PathBuf,
    conn: Connection,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PragmaState {
    pub journal_mode: String,
    pub foreign_keys: i64,
    pub busy_timeout_ms: i64,
}

impl Database {
    /// Open the ZANA SQLite database at `path` with the accepted pragmas.
    ///
    /// Parent directories are created, symlinked database files are rejected,
    /// and existing files are opened without creating or modifying schema.
    /// Migration/schema work remains with the accepted Python evidence until
    /// a later Rust parity cutover owns it.
    pub fn open(path: PathBuf) -> Result<Self, CoreError> {
        reject_symlink_or_other_metadata_error(&path)?;
        let conn = open_connection(&path)?;
        Ok(Self { path, conn })
    }

    /// Apply the idempotent operational schema and return its revision.
    pub fn migrate(&self) -> Result<&'static str, CoreError> {
        self.conn
            .execute_batch(OPERATIONAL_SCHEMA)
            .map_err(|_| CoreError::database())?;
        Ok(OPERATIONAL_REVISION)
    }

    /// Open an additional short-lived connection to the same database file.
    ///
    /// WAL mode allows concurrent readers and one writer; every connection
    /// applies the same bounded pragmas and fails closed on invalid files.
    pub fn connect(&self) -> Result<Connection, CoreError> {
        open_connection(&self.path)
    }

    pub fn pragma_state(&self) -> Result<PragmaState, CoreError> {
        let journal_mode: String = self
            .conn
            .query_row("PRAGMA journal_mode", [], |row| row.get(0))
            .map_err(|_| CoreError::database())?;
        let foreign_keys: i64 = self
            .conn
            .query_row("PRAGMA foreign_keys", [], |row| row.get(0))
            .map_err(|_| CoreError::database())?;
        let busy_timeout_ms: i64 = self
            .conn
            .query_row("PRAGMA busy_timeout", [], |row| row.get(0))
            .map_err(|_| CoreError::database())?;
        Ok(PragmaState {
            journal_mode,
            foreign_keys,
            busy_timeout_ms,
        })
    }

    pub fn close(self) {
        drop(self.conn);
    }
}

fn open_connection(path: &std::path::Path) -> Result<Connection, CoreError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|_| CoreError::database())?;
    }
    let conn = Connection::open(path).map_err(|_| CoreError::database())?;
    conn.busy_timeout(Duration::from_millis(30_000))
        .map_err(|_| CoreError::database())?;
    conn.pragma_update(None, "journal_mode", "WAL")
        .map_err(|_| CoreError::database())?;
    conn.pragma_update(None, "foreign_keys", "ON")
        .map_err(|_| CoreError::database())?;
    // Force SQLite to validate the file now so a non-SQLite path fails
    // honestly at open instead of lazily during the first request.
    conn.query_row("SELECT 1", [], |_| Ok(()))
        .map_err(|_| CoreError::database())?;
    Ok(conn)
}

/// Fail closed before creating or opening a database path.
///
/// `NotFound` is the only metadata error that may proceed; a symlink and any
/// other metadata error reject so an unreadable or smuggled path is never
/// silently created.
fn reject_symlink_or_other_metadata_error(path: &std::path::Path) -> Result<(), CoreError> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(CoreError::database()),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(CoreError::database()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_db(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("zana-core-db-test-{}-{}", std::process::id(), name));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("creates temp dir");
        dir.join("zana.sqlite3")
    }

    #[test]
    fn open_creates_parents_and_required_pragmas() {
        let path = temp_db("open");
        let database = Database::open(path.clone()).expect("opens");
        let state = database.pragma_state().expect("reads pragmas");
        assert_eq!(state.journal_mode, "wal");
        assert_eq!(state.foreign_keys, 1);
        assert_eq!(state.busy_timeout_ms, 30_000);
        database.close();
        let _ = std::fs::remove_dir_all(path.parent().expect("parent"));
    }

    #[test]
    fn open_does_not_touch_existing_schema() {
        let path = temp_db("existing");
        let parent = path.parent().expect("parent");
        std::fs::create_dir_all(parent).expect("creates parent");
        let setup = Connection::open(&path).expect("opens setup db");
        setup
            .execute_batch(
                "CREATE TABLE evidence_marker (id INTEGER PRIMARY KEY); INSERT INTO evidence_marker (id) VALUES (7);",
            )
            .expect("creates marker table");
        drop(setup);

        let database = Database::open(path.clone()).expect("reopens");
        let marker: i64 = database
            .conn
            .query_row("SELECT id FROM evidence_marker", [], |row| row.get(0))
            .expect("marker survives");
        assert_eq!(marker, 7);
        let version_exists: bool = database
            .conn
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name = 'alembic_version')",
                [],
                |row| row.get(0),
            )
            .expect("queries schema");
        assert!(!version_exists, "Rust open must not fake migration state");
        database.close();
        let _ = std::fs::remove_dir_all(parent);
    }

    #[test]
    fn symlinked_database_file_is_rejected() {
        #[cfg(unix)]
        {
            let path = temp_db("symlink");
            let parent = path.parent().expect("parent");
            std::fs::create_dir_all(parent).expect("creates parent");
            std::fs::write(&path, b"not a symlink target").expect("writes placeholder");
            let link = parent.join("link.sqlite3");
            std::os::unix::fs::symlink(&path, &link).expect("creates symlink");
            assert!(Database::open(link).is_err());
            let _ = std::fs::remove_dir_all(parent);
        }
    }

    #[test]
    fn open_rejects_non_sqlite_content_honestly() {
        let path = temp_db("corrupt");
        let parent = path.parent().expect("parent");
        std::fs::create_dir_all(parent).expect("creates parent");
        std::fs::write(&path, b"not a sqlite database").expect("writes file");
        assert!(Database::open(path.clone()).is_err());
        let _ = std::fs::remove_dir_all(parent);
    }

    #[test]
    fn metadata_helper_distinguishes_all_branches() {
        let dir = std::env::temp_dir().join(format!("zana-core-db-meta-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("creates temp dir");

        let missing = dir.join("missing.sqlite3");
        assert!(reject_symlink_or_other_metadata_error(&missing).is_ok());

        let regular = dir.join("regular.sqlite3");
        std::fs::write(&regular, b"data").expect("writes regular file");
        assert!(reject_symlink_or_other_metadata_error(&regular).is_ok());

        #[cfg(unix)]
        {
            let target = dir.join("target.sqlite3");
            std::fs::write(&target, b"data").expect("writes target");
            let link = dir.join("link.sqlite3");
            std::os::unix::fs::symlink(&target, &link).expect("creates symlink");
            assert!(reject_symlink_or_other_metadata_error(&link).is_err());
        }

        // A path under a regular file is not NotFound; it must reject.
        let parent_file = dir.join("file.sqlite3");
        std::fs::write(&parent_file, b"file").expect("writes parent file");
        assert!(reject_symlink_or_other_metadata_error(&parent_file.join("db")).is_err());

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn other_metadata_errors_reject_before_database_creation() {
        let dir =
            std::env::temp_dir().join(format!("zana-core-db-metadata-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("creates temp dir");
        let parent_file = dir.join("not-a-directory");
        std::fs::write(&parent_file, b"file").expect("writes parent file");
        let path = parent_file.join("db").join("zana.sqlite3");

        assert!(Database::open(path.clone()).is_err());
        assert!(!path.exists(), "no database was created through a file");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
