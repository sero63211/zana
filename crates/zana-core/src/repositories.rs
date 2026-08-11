//! SQLite repositories for the durable operational entities.

use rusqlite::{params, Connection, OptionalExtension};
use serde_json::Value;

use crate::domain::{
    JobKind, JobStatus, ModelIdentityStrength, RuntimeKind, RuntimeSource, RuntimeStatus,
};
use crate::error::CoreError;
use crate::time::now_iso;

pub const STREAM_MAX_MESSAGE_CHARS: usize = 256;
pub const STREAM_MAX_PHASE_CHARS: usize = 24;
pub const STREAM_MAX_KIND_CHARS: usize = 32;
pub const STREAM_MAX_ERROR_BYTES: usize = 1024;
pub const MAX_EVENT_PAGE_SIZE: usize = 100;

#[derive(Debug, Clone)]
pub struct RuntimeRow {
    pub id: i64,
    pub kind: RuntimeKind,
    pub endpoint: String,
    pub source: RuntimeSource,
    pub status: RuntimeStatus,
    pub metadata_json: Value,
    pub last_seen_at: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ModelRow {
    pub key: String,
    pub runtime_id: i64,
    pub model_id: String,
    pub digest: Option<String>,
    pub family: Option<String>,
    pub format: Option<String>,
    pub quantization: Option<String>,
    pub parameter_count: Option<i64>,
    pub size_bytes: Option<i64>,
    pub context_length: Option<i64>,
    pub capabilities_json: Vec<String>,
    pub identity_strength: ModelIdentityStrength,
    pub metadata_json: Value,
    pub last_seen_at: Option<String>,
}

#[derive(Debug, Clone)]
pub struct JobRow {
    pub id: i64,
    pub kind: JobKind,
    pub status: JobStatus,
    pub progress_0_1: f64,
    pub phase: String,
    pub message: String,
    pub error_json: Option<Value>,
}

#[derive(Debug, Clone)]
pub struct JobEventStreamRow {
    pub id: i64,
    pub job_id: i64,
    pub kind: String,
    pub phase: String,
    pub message: String,
    pub progress_0_1: f64,
    pub error_json: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Clone)]
pub struct SettingRow {
    pub key: String,
    pub value: Value,
    pub sensitive: bool,
    pub updated_at: String,
}

#[derive(Debug, Clone)]
pub struct AuditEventRow {
    pub sequence: i64,
    pub event_id: String,
    pub line: String,
    pub bytes: i64,
    pub received_at: String,
}

#[derive(Debug, Clone)]
pub struct ResourceSnapshotRow {
    pub revision: i64,
    pub captured_at: String,
    pub platform: String,
    pub os_name: String,
    pub arch: String,
    pub logical_cores: Option<i64>,
    pub memory_total_bytes: Option<i64>,
    pub memory_available_bytes: Option<i64>,
    pub disk_free_bytes: Option<i64>,
    pub probe_error_code: Option<String>,
    pub probe_status: String,
}

fn parse_value(text: &str) -> Value {
    serde_json::from_str(text).unwrap_or(Value::Null)
}

fn parse_capabilities(text: &str) -> Vec<String> {
    let Ok(value) = serde_json::from_str::<Value>(text) else {
        return Vec::new();
    };
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

fn sql_text(value: &str) -> String {
    // Store the exact raw SQLite projection text; JSON values pass through as
    // the bounded string the server surface parses.
    value.to_owned()
}

pub struct Runtimes;

impl Runtimes {
    pub fn get(conn: &Connection, id: i64) -> Result<Option<RuntimeRow>, CoreError> {
        conn.query_row(
            "SELECT id, kind, endpoint, source, status, metadata_json, last_seen_at
             FROM runtimes WHERE id = ?1",
            params![id],
            runtime_row,
        )
        .optional()
        .map_err(|_| CoreError::database())
    }

    pub fn list(conn: &Connection) -> Result<Vec<RuntimeRow>, CoreError> {
        let mut statement = conn
            .prepare(
                "SELECT id, kind, endpoint, source, status, metadata_json, last_seen_at
                 FROM runtimes ORDER BY id",
            )
            .map_err(|_| CoreError::database())?;
        let rows = statement
            .query_map([], runtime_row)
            .map_err(|_| CoreError::database())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| CoreError::database())?;
        Ok(rows)
    }

    pub fn get_by_kind_endpoint(
        conn: &Connection,
        kind: RuntimeKind,
        endpoint: &str,
        source: RuntimeSource,
    ) -> Result<Option<RuntimeRow>, CoreError> {
        conn.query_row(
            "SELECT id, kind, endpoint, source, status, metadata_json, last_seen_at
             FROM runtimes
             WHERE kind = ?1 AND endpoint = ?2 AND source = ?3",
            params![kind.as_str(), endpoint, source.as_str()],
            runtime_row,
        )
        .optional()
        .map_err(|_| CoreError::database())
    }

    pub fn list_manual(conn: &Connection, limit: usize) -> Result<Vec<RuntimeRow>, CoreError> {
        let mut statement = conn
            .prepare(
                "SELECT id, kind, endpoint, source, status, metadata_json, last_seen_at
                 FROM runtimes WHERE source = 'manual' ORDER BY id LIMIT ?1",
            )
            .map_err(|_| CoreError::database())?;
        let rows = statement
            .query_map(params![limit as i64], runtime_row)
            .map_err(|_| CoreError::database())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| CoreError::database())?;
        Ok(rows)
    }

    pub fn insert(
        conn: &Connection,
        kind: RuntimeKind,
        endpoint: &str,
        source: RuntimeSource,
        status: RuntimeStatus,
        metadata_json: &Value,
        last_seen_at: &str,
    ) -> Result<i64, CoreError> {
        conn.execute(
            "INSERT INTO runtimes (kind, endpoint, source, status, metadata_json, last_seen_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                kind.as_str(),
                endpoint,
                source.as_str(),
                status.as_str(),
                serde_json::to_string(metadata_json).map_err(|_| CoreError::database())?,
                last_seen_at
            ],
        )
        .map_err(|_| CoreError::database())?;
        Ok(conn.last_insert_rowid())
    }

    pub fn update(
        conn: &Connection,
        id: i64,
        status: RuntimeStatus,
        metadata_json: &Value,
        last_seen_at: &str,
    ) -> Result<(), CoreError> {
        conn.execute(
            "UPDATE runtimes SET status = ?1, metadata_json = ?2, last_seen_at = ?3
             WHERE id = ?4",
            params![
                status.as_str(),
                serde_json::to_string(metadata_json).map_err(|_| CoreError::database())?,
                last_seen_at,
                id
            ],
        )
        .map_err(|_| CoreError::database())?;
        Ok(())
    }

    pub fn delete(conn: &Connection, id: i64) -> Result<(), CoreError> {
        conn.execute("DELETE FROM runtimes WHERE id = ?1", params![id])
            .map_err(|_| CoreError::database())?;
        Ok(())
    }
}

fn runtime_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<RuntimeRow> {
    let kind: String = row.get(1)?;
    let source: String = row.get(3)?;
    let status: String = row.get(4)?;
    let metadata: String = row.get(5)?;
    Ok(RuntimeRow {
        id: row.get(0)?,
        kind: RuntimeKind::parse(&kind),
        endpoint: row.get(2)?,
        source: RuntimeSource::parse(&source).unwrap_or(RuntimeSource::Auto),
        status: RuntimeStatus::parse(&status),
        metadata_json: parse_value(&metadata),
        last_seen_at: row.get(6)?,
    })
}

pub struct Models;

impl Models {
    pub fn get(conn: &Connection, key: &str) -> Result<Option<ModelRow>, CoreError> {
        conn.query_row(
            "SELECT key, runtime_id, model_id, digest, family, format, quantization,
                    parameter_count, size_bytes, context_length, capabilities_json,
                    identity_strength, metadata_json, last_seen_at
             FROM models WHERE key = ?1",
            params![key],
            model_row,
        )
        .optional()
        .map_err(|_| CoreError::database())
    }

    pub fn list(conn: &Connection) -> Result<Vec<ModelRow>, CoreError> {
        list_models_query(
            conn,
            "SELECT key, runtime_id, model_id, digest, family, format, quantization,
                    parameter_count, size_bytes, context_length, capabilities_json,
                    identity_strength, metadata_json, last_seen_at
             FROM models ORDER BY key",
            params![],
        )
    }

    pub fn list_by_runtime(conn: &Connection, runtime_id: i64) -> Result<Vec<ModelRow>, CoreError> {
        list_models_query(
            conn,
            "SELECT key, runtime_id, model_id, digest, family, format, quantization,
                    parameter_count, size_bytes, context_length, capabilities_json,
                    identity_strength, metadata_json, last_seen_at
             FROM models WHERE runtime_id = ?1 ORDER BY key",
            params![runtime_id],
        )
    }

    pub fn list_by_capability(
        conn: &Connection,
        capability: &str,
    ) -> Result<Vec<ModelRow>, CoreError> {
        let needle = format!("\"{capability}\"");
        list_models_query(
            conn,
            "SELECT key, runtime_id, model_id, digest, family, format, quantization,
                    parameter_count, size_bytes, context_length, capabilities_json,
                    identity_strength, metadata_json, last_seen_at
             FROM models WHERE capabilities_json LIKE ?1 ORDER BY key",
            params![format!("%{needle}%")],
        )
    }

    pub fn list_runnable(conn: &Connection) -> Result<Vec<ModelRow>, CoreError> {
        list_models_query(
            conn,
            "SELECT m.key, m.runtime_id, m.model_id, m.digest, m.family, m.format,
                    m.quantization, m.parameter_count, m.size_bytes, m.context_length,
                    m.capabilities_json, m.identity_strength, m.metadata_json, m.last_seen_at
             FROM models m JOIN runtimes r ON r.id = m.runtime_id
             WHERE r.status = 'online' ORDER BY m.key",
            params![],
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn upsert(
        conn: &Connection,
        key: &str,
        runtime_id: i64,
        model_id: &str,
        digest: Option<&str>,
        family: Option<&str>,
        format: Option<&str>,
        quantization: Option<&str>,
        parameter_count: Option<i64>,
        size_bytes: Option<i64>,
        context_length: Option<i64>,
        capabilities: &[String],
        identity_strength: ModelIdentityStrength,
        metadata_json: &Value,
        last_seen_at: &str,
    ) -> Result<(), CoreError> {
        conn.execute(
            "INSERT INTO models (key, runtime_id, model_id, digest, family, format,
                                 quantization, parameter_count, size_bytes, context_length,
                                 capabilities_json, identity_strength, metadata_json, last_seen_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)
             ON CONFLICT(key) DO UPDATE SET
                model_id = excluded.model_id,
                digest = excluded.digest,
                family = excluded.family,
                format = excluded.format,
                quantization = excluded.quantization,
                parameter_count = excluded.parameter_count,
                size_bytes = excluded.size_bytes,
                context_length = excluded.context_length,
                capabilities_json = excluded.capabilities_json,
                identity_strength = excluded.identity_strength,
                metadata_json = excluded.metadata_json,
                last_seen_at = excluded.last_seen_at",
            params![
                key,
                runtime_id,
                model_id,
                digest,
                family,
                format,
                quantization,
                parameter_count,
                size_bytes,
                context_length,
                serde_json::to_string(capabilities).map_err(|_| CoreError::database())?,
                identity_strength.as_str(),
                serde_json::to_string(metadata_json).map_err(|_| CoreError::database())?,
                last_seen_at
            ],
        )
        .map_err(|_| CoreError::database())?;
        Ok(())
    }

    pub fn delete_keys(conn: &Connection, keys: &[String]) -> Result<(), CoreError> {
        for key in keys {
            conn.execute("DELETE FROM models WHERE key = ?1", params![key])
                .map_err(|_| CoreError::database())?;
        }
        Ok(())
    }
}

fn model_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<ModelRow> {
    let capabilities: String = row.get(10)?;
    let strength: String = row.get(11)?;
    let metadata: String = row.get(12)?;
    Ok(ModelRow {
        key: row.get(0)?,
        runtime_id: row.get(1)?,
        model_id: row.get(2)?,
        digest: row.get(3)?,
        family: row.get(4)?,
        format: row.get(5)?,
        quantization: row.get(6)?,
        parameter_count: row.get(7)?,
        size_bytes: row.get(8)?,
        context_length: row.get(9)?,
        capabilities_json: parse_capabilities(&capabilities),
        identity_strength: ModelIdentityStrength::parse(&strength),
        metadata_json: parse_value(&metadata),
        last_seen_at: row.get(13)?,
    })
}

fn list_models_query(
    conn: &Connection,
    sql: &str,
    params: impl rusqlite::Params,
) -> Result<Vec<ModelRow>, CoreError> {
    let mut statement = conn.prepare(sql).map_err(|_| CoreError::database())?;
    let rows = statement
        .query_map(params, model_row)
        .map_err(|_| CoreError::database())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| CoreError::database())?;
    Ok(rows)
}

pub struct Jobs;

impl Jobs {
    pub fn insert(
        conn: &Connection,
        kind: JobKind,
        phase: &str,
        message: &str,
    ) -> Result<i64, CoreError> {
        conn.execute(
            "INSERT INTO jobs (kind, status, progress_0_1, phase, message, error_json)
             VALUES (?1, 'PENDING', 0, ?2, ?3, NULL)",
            params![kind.as_str(), phase, message],
        )
        .map_err(|_| CoreError::database())?;
        Ok(conn.last_insert_rowid())
    }

    pub fn get(conn: &Connection, id: i64) -> Result<Option<JobRow>, CoreError> {
        conn.query_row(
            "SELECT id, kind, status, progress_0_1, phase, message, error_json
             FROM jobs WHERE id = ?1",
            params![id],
            job_row,
        )
        .optional()
        .map_err(|_| CoreError::database())
    }

    pub fn list_active(conn: &Connection) -> Result<Vec<JobRow>, CoreError> {
        let mut statement = conn
            .prepare(
                "SELECT id, kind, status, progress_0_1, phase, message, error_json
                 FROM jobs WHERE status IN ('PENDING', 'RUNNING') ORDER BY id",
            )
            .map_err(|_| CoreError::database())?;
        let rows = statement
            .query_map([], job_row)
            .map_err(|_| CoreError::database())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| CoreError::database())?;
        Ok(rows)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn update(
        conn: &Connection,
        id: i64,
        status: JobStatus,
        phase: &str,
        message: &str,
        progress_0_1: f64,
        error_json: Option<&Value>,
    ) -> Result<(), CoreError> {
        conn.execute(
            "UPDATE jobs SET status = ?1, phase = ?2, message = ?3, progress_0_1 = ?4,
             error_json = ?5 WHERE id = ?6",
            params![
                status.as_str(),
                phase,
                message,
                progress_0_1,
                error_json
                    .map(|value| serde_json::to_string(value).unwrap_or_else(|_| "{}".to_owned())),
                id
            ],
        )
        .map_err(|_| CoreError::database())?;
        Ok(())
    }
}

fn job_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<JobRow> {
    let kind: String = row.get(1)?;
    let status: String = row.get(2)?;
    let error: Option<String> = row.get(6)?;
    Ok(JobRow {
        id: row.get(0)?,
        kind: JobKind::parse(&kind),
        status: JobStatus::parse(&status).unwrap_or(JobStatus::Pending),
        progress_0_1: row.get(3)?,
        phase: row.get(4)?,
        message: row.get(5)?,
        error_json: error.as_deref().map(parse_value),
    })
}

pub struct JobEvents;

impl JobEvents {
    #[allow(clippy::too_many_arguments)]
    pub fn insert(
        conn: &Connection,
        job_id: i64,
        kind: &str,
        phase: &str,
        message: &str,
        progress_0_1: f64,
        error_json: Option<&Value>,
    ) -> Result<i64, CoreError> {
        conn.execute(
            "INSERT INTO job_events (job_id, kind, phase, message, progress_0_1, error_json, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                job_id,
                kind,
                phase,
                message,
                progress_0_1,
                error_json
                    .map(|value| serde_json::to_string(value).unwrap_or_else(|_| "{}".to_owned())),
                now_iso()
            ],
        )
        .map_err(|_| CoreError::database())?;
        Ok(conn.last_insert_rowid())
    }

    /// Read one bounded ascending page with SQL-side truncation and an error
    /// sentinel, exactly matching the accepted fetch/SSE projection.
    pub fn list_for_job_stream(
        conn: &Connection,
        job_id: i64,
        after_event_id: i64,
        limit: usize,
    ) -> Result<Vec<JobEventStreamRow>, CoreError> {
        if !(1..=MAX_EVENT_PAGE_SIZE).contains(&limit) {
            return Err(CoreError::database());
        }
        if after_event_id < 0 || job_id < 0 {
            return Err(CoreError::database());
        }
        let sql = format!(
            "SELECT id, job_id,
                    substr(kind, 1, {kind}),
                    substr(phase, 1, {phase}),
                    substr(message, 1, {message}),
                    progress_0_1,
                    CASE
                        WHEN error_json IS NULL THEN NULL
                        WHEN length(error_json) > {error} THEN '{{\"code\":\"REDACTED_ERROR\",\"message\":\"[truncated]\"}}'
                        ELSE error_json
                    END,
                    created_at
             FROM job_events
             WHERE job_id = ?1 AND id > ?2
             ORDER BY id ASC LIMIT ?3",
            kind = STREAM_MAX_KIND_CHARS,
            phase = STREAM_MAX_PHASE_CHARS,
            message = STREAM_MAX_MESSAGE_CHARS,
            error = STREAM_MAX_ERROR_BYTES,
        );
        let mut statement = conn.prepare(&sql).map_err(|_| CoreError::database())?;
        let rows = statement
            .query_map(params![job_id, after_event_id, limit as i64], stream_row)
            .map_err(|_| CoreError::database())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| CoreError::database())?;
        Ok(rows)
    }

    pub fn delete_before_id(
        conn: &Connection,
        job_id: i64,
        keep_from_id: i64,
    ) -> Result<(), CoreError> {
        conn.execute(
            "DELETE FROM job_events WHERE job_id = ?1 AND id < ?2",
            params![job_id, keep_from_id],
        )
        .map_err(|_| CoreError::database())?;
        Ok(())
    }

    pub fn oldest_id_after_trim(
        conn: &Connection,
        job_id: i64,
        retain_count: i64,
    ) -> Result<i64, CoreError> {
        conn.query_row(
            "SELECT COALESCE(
                (SELECT id FROM job_events WHERE job_id = ?1
                 ORDER BY id DESC LIMIT 1 OFFSET ?2), 0)",
            params![job_id, retain_count - 1],
            |row| row.get(0),
        )
        .map_err(|_| CoreError::database())
    }
}

fn stream_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<JobEventStreamRow> {
    Ok(JobEventStreamRow {
        id: row.get(0)?,
        job_id: row.get(1)?,
        kind: sql_text(&row.get::<_, String>(2)?),
        phase: row.get(3)?,
        message: row.get(4)?,
        progress_0_1: row.get(5)?,
        error_json: row.get(6)?,
        created_at: row.get(7)?,
    })
}

pub struct Settings;

impl Settings {
    pub fn get(conn: &Connection, key: &str) -> Result<Option<SettingRow>, CoreError> {
        conn.query_row(
            "SELECT key, value_json, sensitive, updated_at FROM settings WHERE key = ?1",
            params![key],
            |row| {
                let value: String = row.get(1)?;
                Ok(SettingRow {
                    key: row.get(0)?,
                    value: parse_value(&value),
                    sensitive: row.get::<_, i64>(2)? != 0,
                    updated_at: row.get(3)?,
                })
            },
        )
        .optional()
        .map_err(|_| CoreError::database())
    }

    pub fn list(conn: &Connection) -> Result<Vec<SettingRow>, CoreError> {
        let mut statement = conn
            .prepare("SELECT key, value_json, sensitive, updated_at FROM settings ORDER BY key")
            .map_err(|_| CoreError::database())?;
        let rows = statement
            .query_map([], |row| {
                let value: String = row.get(1)?;
                Ok(SettingRow {
                    key: row.get(0)?,
                    value: parse_value(&value),
                    sensitive: row.get::<_, i64>(2)? != 0,
                    updated_at: row.get(3)?,
                })
            })
            .map_err(|_| CoreError::database())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| CoreError::database())?;
        Ok(rows)
    }

    pub fn upsert(
        conn: &Connection,
        key: &str,
        value: &Value,
        sensitive: bool,
    ) -> Result<(), CoreError> {
        conn.execute(
            "INSERT INTO settings (key, value_json, sensitive, updated_at)
             VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
                sensitive = excluded.sensitive, updated_at = excluded.updated_at",
            params![
                key,
                serde_json::to_string(value).map_err(|_| CoreError::database())?,
                i64::from(sensitive),
                now_iso()
            ],
        )
        .map_err(|_| CoreError::database())?;
        Ok(())
    }

    pub fn delete(conn: &Connection, key: &str) -> Result<(), CoreError> {
        conn.execute("DELETE FROM settings WHERE key = ?1", params![key])
            .map_err(|_| CoreError::database())?;
        Ok(())
    }
}

pub struct AuditEvents;

impl AuditEvents {
    pub fn insert(
        conn: &Connection,
        event_id: &str,
        line: &str,
        bytes: i64,
    ) -> Result<i64, CoreError> {
        conn.execute(
            "INSERT INTO audit_events (event_id, line, bytes, received_at)
             VALUES (?1, ?2, ?3, ?4)",
            params![event_id, line, bytes, now_iso()],
        )
        .map_err(|_| CoreError::database())?;
        Ok(conn.last_insert_rowid())
    }

    pub fn page(
        conn: &Connection,
        limit: usize,
        before_sequence: Option<i64>,
    ) -> Result<Vec<AuditEventRow>, CoreError> {
        let mut statement = conn
            .prepare(
                "SELECT sequence, event_id, line, bytes, received_at
                 FROM audit_events
                 WHERE (?1 IS NULL OR sequence < ?1)
                 ORDER BY sequence DESC LIMIT ?2",
            )
            .map_err(|_| CoreError::database())?;
        let rows = statement
            .query_map(params![before_sequence, limit as i64], |row| {
                Ok(AuditEventRow {
                    sequence: row.get(0)?,
                    event_id: row.get(1)?,
                    line: row.get(2)?,
                    bytes: row.get(3)?,
                    received_at: row.get(4)?,
                })
            })
            .map_err(|_| CoreError::database())?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| CoreError::database())?;
        Ok(rows)
    }

    pub fn trim_oldest(conn: &Connection, retain_count: i64) -> Result<(), CoreError> {
        conn.execute(
            "DELETE FROM audit_events WHERE sequence NOT IN (
                SELECT sequence FROM audit_events ORDER BY sequence DESC LIMIT ?1)",
            params![retain_count],
        )
        .map_err(|_| CoreError::database())?;
        Ok(())
    }

    pub fn total_bytes(conn: &Connection) -> Result<i64, CoreError> {
        conn.query_row(
            "SELECT COALESCE(SUM(bytes), 0) FROM audit_events",
            [],
            |row| row.get(0),
        )
        .map_err(|_| CoreError::database())
    }
}

pub struct ResourceSnapshots;

impl ResourceSnapshots {
    #[allow(clippy::too_many_arguments)]
    pub fn save(
        conn: &Connection,
        revision: i64,
        captured_at: &str,
        platform: &str,
        os_name: &str,
        arch: &str,
        logical_cores: Option<i64>,
        memory_total_bytes: Option<i64>,
        memory_available_bytes: Option<i64>,
        disk_free_bytes: Option<i64>,
        probe_error_code: Option<&str>,
        probe_status: &str,
    ) -> Result<(), CoreError> {
        conn.execute(
            "INSERT INTO resource_snapshots (revision, captured_at, platform, os_name, arch,
                                             logical_cores, memory_total_bytes, memory_available_bytes,
                                             disk_free_bytes, probe_error_code, probe_status)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)
             ON CONFLICT(revision) DO UPDATE SET
                captured_at = excluded.captured_at,
                platform = excluded.platform,
                os_name = excluded.os_name,
                arch = excluded.arch,
                logical_cores = excluded.logical_cores,
                memory_total_bytes = excluded.memory_total_bytes,
                memory_available_bytes = excluded.memory_available_bytes,
                disk_free_bytes = excluded.disk_free_bytes,
                probe_error_code = excluded.probe_error_code,
                probe_status = excluded.probe_status",
            params![
                revision,
                captured_at,
                platform,
                os_name,
                arch,
                logical_cores,
                memory_total_bytes,
                memory_available_bytes,
                disk_free_bytes,
                probe_error_code,
                probe_status
            ],
        )
        .map_err(|_| CoreError::database())?;
        Ok(())
    }

    pub fn latest(conn: &Connection) -> Result<Option<ResourceSnapshotRow>, CoreError> {
        conn.query_row(
            "SELECT revision, captured_at, platform, os_name, arch, logical_cores,
                    memory_total_bytes, memory_available_bytes, disk_free_bytes,
                    probe_error_code, probe_status
             FROM resource_snapshots ORDER BY revision DESC LIMIT 1",
            [],
            |row| {
                Ok(ResourceSnapshotRow {
                    revision: row.get(0)?,
                    captured_at: row.get(1)?,
                    platform: row.get(2)?,
                    os_name: row.get(3)?,
                    arch: row.get(4)?,
                    logical_cores: row.get(5)?,
                    memory_total_bytes: row.get(6)?,
                    memory_available_bytes: row.get(7)?,
                    disk_free_bytes: row.get(8)?,
                    probe_error_code: row.get(9)?,
                    probe_status: row.get(10)?,
                })
            },
        )
        .optional()
        .map_err(|_| CoreError::database())
    }
}
