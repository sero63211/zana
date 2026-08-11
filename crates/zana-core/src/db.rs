//! SQLite bootstrap and open behavior for the Rust Core.

use std::path::PathBuf;
use std::time::Duration;

use rusqlite::Connection;

use crate::error::CoreError;

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
        if let Ok(metadata) = std::fs::symlink_metadata(&path) {
            if metadata.file_type().is_symlink() {
                return Err(CoreError::database());
            }
        }
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|_| CoreError::database())?;
        }

        let conn = Connection::open(&path).map_err(|_| CoreError::database())?;
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

        Ok(Self { path, conn })
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
}
