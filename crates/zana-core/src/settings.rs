//! Bounded persistent settings with sensitive-value redaction.

use rusqlite::Connection;
use serde_json::Value;

use crate::repositories::{SettingRow, Settings as SettingsRepo};

pub const MAX_SETTING_KEY_CHARS: usize = 128;
pub const MAX_SETTING_VALUE_BYTES: usize = 4096;

#[derive(Debug, Clone)]
pub struct SettingView {
    pub key: String,
    pub value: Value,
    pub sensitive: bool,
    pub updated_at: String,
}

#[derive(Debug)]
pub enum SettingsError {
    InvalidKey,
    InvalidValue,
    Storage,
}

pub struct SettingsService;

impl SettingsService {
    pub fn get(conn: &Connection, key: &str) -> Result<Option<SettingView>, SettingsError> {
        validate_key(key)?;
        SettingsRepo::get(conn, key)
            .map_err(|_| SettingsError::Storage)
            .map(|row| row.map(project))
    }

    pub fn list(conn: &Connection) -> Result<Vec<SettingView>, SettingsError> {
        SettingsRepo::list(conn)
            .map_err(|_| SettingsError::Storage)
            .map(|rows| rows.into_iter().map(project).collect())
    }

    pub fn set(
        conn: &Connection,
        key: &str,
        value: Value,
        sensitive: bool,
    ) -> Result<SettingView, SettingsError> {
        validate_key(key)?;
        validate_value(&value)?;
        SettingsRepo::upsert(conn, key, &value, sensitive).map_err(|_| SettingsError::Storage)?;
        Self::get(conn, key)?.ok_or(SettingsError::Storage)
    }

    pub fn delete(conn: &Connection, key: &str) -> Result<(), SettingsError> {
        validate_key(key)?;
        SettingsRepo::delete(conn, key).map_err(|_| SettingsError::Storage)
    }
}

fn project(row: SettingRow) -> SettingView {
    let value = if row.sensitive {
        Value::String("***".to_owned())
    } else {
        row.value
    };
    SettingView {
        key: row.key,
        value,
        sensitive: row.sensitive,
        updated_at: row.updated_at,
    }
}

fn validate_key(key: &str) -> Result<(), SettingsError> {
    if key.is_empty()
        || key.len() > MAX_SETTING_KEY_CHARS
        || !key.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"._-".contains(&byte)
        })
    {
        return Err(SettingsError::InvalidKey);
    }
    Ok(())
}

fn validate_value(value: &Value) -> Result<(), SettingsError> {
    let encoded = serde_json::to_vec(value).map_err(|_| SettingsError::InvalidValue)?;
    if encoded.len() > MAX_SETTING_VALUE_BYTES {
        return Err(SettingsError::InvalidValue);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::Database;

    fn test_db(name: &str) -> (Database, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "zana-settings-test-{}-{}",
            std::process::id(),
            name
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join("db").join("zana.sqlite3");
        let database = Database::open(path.clone()).expect("opens");
        database.migrate().expect("migrates");
        (database, dir)
    }

    #[test]
    fn stores_and_redacts_sensitive_values() {
        let (database, dir) = test_db("roundtrip");
        let connection = database.connect().expect("connects");
        let saved = SettingsService::set(
            &connection,
            "security.expose_token",
            serde_json::json!("abc123"),
            true,
        )
        .expect("saves");
        assert_eq!(saved.value, Value::String("***".to_owned()));
        let stored = SettingsRepo::get(&connection, "security.expose_token")
            .expect("reads raw")
            .expect("row exists");
        assert_eq!(stored.value, Value::String("abc123".to_owned()));
        let listed = SettingsService::list(&connection).expect("lists");
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].value, Value::String("***".to_owned()));
        drop(connection);
        database.close();
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn rejects_hostile_keys_and_oversized_values() {
        let (database, dir) = test_db("validation");
        let connection = database.connect().expect("connects");
        assert!(matches!(
            SettingsService::set(&connection, "../escape", Value::Bool(true), false),
            Err(SettingsError::InvalidKey)
        ));
        let huge = serde_json::json!("x".repeat(MAX_SETTING_VALUE_BYTES + 1));
        assert!(matches!(
            SettingsService::set(&connection, "valid.key", huge, false),
            Err(SettingsError::InvalidValue)
        ));
        drop(connection);
        database.close();
        let _ = std::fs::remove_dir_all(dir);
    }
}
