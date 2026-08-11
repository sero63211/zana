//! Focused tests for the durable operational plane: migrations, settings,
//! jobs, events, retention, and restart recovery.

use rusqlite::Connection;
use zana_core::db::Database;
use zana_core::domain::{JobEventKind, JobKind, JobStatus};
use zana_core::jobs::{JobError, JobService};
use zana_core::repositories::{
    AuditEvents, JobEvents, Jobs, Runtimes, Settings as SettingsRepo, MAX_EVENT_PAGE_SIZE,
    MAX_RETAINED_EVENTS_PER_JOB,
};
use zana_core::settings::{SettingsError, SettingsService};

fn test_db(name: &str) -> (Database, std::path::PathBuf) {
    let dir = std::env::temp_dir().join(format!(
        "zana-operational-test-{}-{}",
        std::process::id(),
        name
    ));
    let _ = std::fs::remove_dir_all(&dir);
    let path = dir.join("db").join("zana.sqlite3");
    let database = Database::open(path.clone()).expect("opens database");
    database.migrate().expect("migrates");
    (database, dir)
}

fn open_conn(database: &Database) -> Connection {
    database.connect().expect("opens connection")
}

#[test]
fn migration_is_idempotent_and_preserves_existing_tables() {
    let dir = std::env::temp_dir().join(format!("zana-operational-compat-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join("db")).expect("creates parent");
    let path = dir.join("db").join("zana.sqlite3");
    {
        let setup = Connection::open(&path).expect("opens setup db");
        setup
            .execute_batch(
                "CREATE TABLE jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_0_1 REAL NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    error_json TEXT
                 );
                 INSERT INTO jobs (kind, status) VALUES ('build', 'SUCCEEDED');",
            )
            .expect("creates python-style jobs table");
    }
    let database = Database::open(path.clone()).expect("opens");
    assert_eq!(
        database.migrate().expect("migrates"),
        "zana-rust-operational-v1"
    );
    database.migrate().expect("second migrate is idempotent");
    let connection = open_conn(&database);
    let preserved: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM jobs WHERE kind = 'build'",
            [],
            |row| row.get(0),
        )
        .expect("reads preserved row");
    assert_eq!(preserved, 1);
    let tables: Vec<String> = connection
        .prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('runtimes', 'models', 'job_events', 'settings', 'resource_snapshots', 'audit_events')")
        .expect("prepares")
        .query_map([], |row| row.get(0))
        .expect("maps")
        .collect::<Result<Vec<_>, _>>()
        .expect("collects");
    assert_eq!(tables.len(), 6);
    let alembic: i64 = connection
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name = 'alembic_version')",
            [],
            |row| row.get(0),
        )
        .expect("queries alembic marker");
    assert_eq!(
        alembic, 0,
        "Rust migration never claims Python migration state"
    );
    drop(connection);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn job_lifecycle_persists_legal_transitions_and_events() {
    let (database, dir) = test_db("jobs");
    let mut conn = open_conn(&database);
    let job = JobService::create_job(&mut conn, JobKind::RuntimeRefresh, "discovery", "start")
        .expect("creates job");
    assert_eq!(job.status, JobStatus::Pending);

    let running = JobService::transition_job(
        &mut conn,
        job.id,
        JobStatus::Running,
        Some("discovery"),
        None,
        Some(0.5),
        None,
    )
    .expect("transitions to running");
    assert_eq!(running.status, JobStatus::Running);
    assert!((running.progress_0_1 - 0.5).abs() < 1e-9);

    let done = JobService::transition_job(
        &mut conn,
        job.id,
        JobStatus::Succeeded,
        Some("complete"),
        Some("done"),
        Some(1.0),
        None,
    )
    .expect("transitions to succeeded");
    assert_eq!(done.status, JobStatus::Succeeded);

    let events = JobService::list_events(&conn, job.id, 0, 50).expect("lists events");
    assert_eq!(events.len(), 3);
    assert_eq!(events[0].kind, JobEventKind::Created.as_str());
    assert_eq!(events[1].kind, JobEventKind::StatusChanged.as_str());
    assert_eq!(events[2].kind, JobEventKind::StatusChanged.as_str());
    assert!(events[2].id > events[1].id, "event ids are monotonic");
    drop(conn);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn invalid_transitions_and_terminal_cancels_fail_cleanly() {
    let (database, dir) = test_db("invalid");
    let mut conn = open_conn(&database);
    let job = JobService::create_job(&mut conn, JobKind::ModelPull, "queued", "").expect("creates");
    assert!(matches!(
        JobService::transition_job(
            &mut conn,
            job.id,
            JobStatus::Succeeded,
            None,
            None,
            None,
            None,
        ),
        Err(JobError::InvalidTransition(_))
    ));

    JobService::transition_job(
        &mut conn,
        job.id,
        JobStatus::Failed,
        Some("failed"),
        Some("boom"),
        None,
        Some(&serde_json::json!({"code": "X"})),
    )
    .expect("fails legally");
    assert!(matches!(
        JobService::cancel_job(&mut conn, job.id, "too late"),
        Err(JobError::InvalidTransition(_))
    ));
    let error_event = JobEvents::list_for_job_stream(&conn, job.id, 0, 50).expect("events");
    assert_eq!(
        error_event.last().expect("last").kind,
        JobEventKind::Error.as_str()
    );
    drop(conn);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn progress_is_clamped_and_rejected_after_terminal() {
    let (database, dir) = test_db("progress");
    let mut conn = open_conn(&database);
    let job = JobService::create_job(&mut conn, JobKind::RuntimeRefresh, "", "").expect("creates");
    let progressed = JobService::record_progress(&mut conn, job.id, 7.0, "phase", "message")
        .expect("records progress");
    assert_eq!(progressed.progress_0_1, 1.0);
    let progressed = JobService::record_progress(&mut conn, job.id, -3.0, "phase", "")
        .expect("records progress");
    assert_eq!(progressed.progress_0_1, 0.0);
    JobService::cancel_job(&mut conn, job.id, "stop").expect("cancels");
    assert!(matches!(
        JobService::record_progress(&mut conn, job.id, 0.5, "phase", "late"),
        Err(JobError::InvalidTransition(_))
    ));
    drop(conn);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn event_stream_projection_truncates_and_sentinels() {
    let (database, dir) = test_db("projection");
    let mut conn = open_conn(&database);
    let job = JobService::create_job(&mut conn, JobKind::ModelPull, "", "").expect("creates");
    let huge_error = serde_json::json!({
        "code": "X",
        "message": "y".repeat(5000)
    });
    JobEvents::insert(
        &conn,
        job.id,
        JobEventKind::Error.as_str(),
        &"p".repeat(200),
        &"m".repeat(4000),
        0.0,
        Some(&huge_error),
    )
    .expect("inserts hostile event");
    let rows = JobEvents::list_for_job_stream(&conn, job.id, 0, 50).expect("stream rows");
    let hostile = rows.last().expect("hostile row");
    assert!(hostile.phase.len() <= 24);
    assert!(hostile.message.len() <= 256);
    let error: serde_json::Value =
        serde_json::from_str(hostile.error_json.as_deref().expect("sentinel")).expect("json");
    assert_eq!(error["code"], "REDACTED_ERROR");

    assert!(matches!(
        JobService::list_events(&conn, job.id, 0, 101),
        Err(JobError::InvalidArgument)
    ));
    assert!(matches!(
        JobService::list_events(&conn, job.id, -1, 10),
        Err(JobError::InvalidArgument)
    ));
    drop(conn);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn retention_trims_oldest_events_only() {
    let (database, dir) = test_db("retention");
    let mut conn = open_conn(&database);
    let job = JobService::create_job(&mut conn, JobKind::RuntimeRefresh, "", "").expect("creates");
    for index in 0..5 {
        JobService::record_progress(&mut conn, job.id, 0.1, "phase", &format!("event-{index}"))
            .expect("records");
    }
    let removed = JobService::enforce_event_retention(&conn, job.id, 3).expect("trims");
    assert_eq!(removed, 3);
    let rows = JobEvents::list_for_job_stream(&conn, job.id, 0, 50).expect("rows");
    assert_eq!(rows.len(), 3);
    assert_eq!(rows[0].id, 4);
    drop(conn);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn restart_recovery_marks_model_pulls_interrupted_only() {
    let (database, dir) = test_db("recovery");
    let mut conn = open_conn(&database);
    let pull = JobService::create_job(&mut conn, JobKind::ModelPull, "queued", "").expect("pull");
    JobService::transition_job(
        &mut conn,
        pull.id,
        JobStatus::Running,
        Some("downloading"),
        None,
        None,
        None,
    )
    .expect("starts pull");
    let refresh =
        JobService::create_job(&mut conn, JobKind::RuntimeRefresh, "", "").expect("refresh");

    let recovered = JobService::recover_interrupted_model_pulls(&mut conn).expect("recovers");
    assert_eq!(recovered, 1);
    let pull_row = Jobs::get(&conn, pull.id).expect("reads").expect("exists");
    assert_eq!(pull_row.status, JobStatus::Failed);
    assert_eq!(pull_row.phase, "interrupted");
    let refresh_row = Jobs::get(&conn, refresh.id)
        .expect("reads")
        .expect("exists");
    assert_eq!(refresh_row.status, JobStatus::Pending);
    drop(conn);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn settings_and_audit_repositories_are_bounded() {
    let (database, dir) = test_db("settings-audit");
    let conn = open_conn(&database);
    assert!(matches!(
        SettingsService::set(&conn, "bad/key", serde_json::json!(1), false),
        Err(SettingsError::InvalidKey)
    ));
    SettingsService::set(
        &conn,
        "observability.enabled",
        serde_json::json!(false),
        false,
    )
    .expect("sets");
    assert_eq!(
        SettingsRepo::get(&conn, "observability.enabled")
            .expect("gets")
            .expect("row")
            .value,
        serde_json::json!(false)
    );

    AuditEvents::insert(&conn, "evt-1", "{\"kind\":\"system\"}\n", 17).expect("audits");
    let page = AuditEvents::page(&conn, 10, None).expect("pages");
    assert_eq!(page.len(), 1);
    assert_eq!(page[0].bytes, 17);
    assert!(AuditEvents::total_bytes(&conn).expect("bytes") >= 17);
    AuditEvents::trim_oldest(&conn, 0).expect("trims");
    assert!(AuditEvents::page(&conn, 10, None)
        .expect("pages")
        .is_empty());
    drop(conn);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn two_writer_transitions_never_produce_conflicting_terminal_states() {
    let (database, dir) = test_db("atomic-transition");
    let path = database.path.clone();
    let mut setup = open_conn(&database);
    let job =
        JobService::create_job(&mut setup, JobKind::ModelPull, "queued", "").expect("creates");
    JobService::transition_job(
        &mut setup,
        job.id,
        JobStatus::Running,
        Some("downloading"),
        None,
        None,
        None,
    )
    .expect("starts");
    drop(setup);
    drop(database);

    let writer_a = std::thread::spawn({
        let path = path.clone();
        move || {
            let mut conn = Connection::open(&path).expect("opens writer a");
            JobService::transition_job(
                &mut conn,
                job.id,
                JobStatus::Succeeded,
                Some("complete"),
                None,
                None,
                None,
            )
        }
    });
    let writer_b = std::thread::spawn({
        let path = path.clone();
        move || {
            let mut conn = Connection::open(&path).expect("opens writer b");
            JobService::transition_job(
                &mut conn,
                job.id,
                JobStatus::Failed,
                Some("failed"),
                None,
                None,
                None,
            )
        }
    });
    let results = [
        writer_a.join().expect("joins"),
        writer_b.join().expect("joins"),
    ];
    let succeeded = results.iter().filter(|result| result.is_ok()).count();
    assert_eq!(succeeded, 1, "exactly one writer wins the transition");
    let final_conn = Connection::open(&path).expect("opens final");
    let final_row = Jobs::get(&final_conn, job.id)
        .expect("reads")
        .expect("exists");
    assert!(final_row.status.is_terminal());
    drop(final_conn);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn unknown_db_enums_fail_closed_instead_of_guessing() {
    let (database, dir) = test_db("decoding");
    let conn = open_conn(&database);
    conn.execute(
        "INSERT INTO runtimes (kind, endpoint, source, status) VALUES ('unknown', 'http://127.0.0.1:1', 'auto', 'unknown')",
        [],
    )
    .expect("inserts exact unknown runtime");
    let unknown_row = Runtimes::list(&conn).expect("lists exact unknown runtime");
    assert_eq!(unknown_row[0].kind, zana_core::domain::RuntimeKind::Unknown);
    conn.execute(
        "INSERT INTO jobs (kind, status, phase, message) VALUES ('corrupt-kind', 'RUNNING', '', '')",
        [],
    )
    .expect("inserts corrupt kind");
    assert!(Jobs::get(&conn, 1).is_err());
    conn.execute(
        "UPDATE runtimes SET status = 'corrupt-status' WHERE id = 1",
        [],
    )
    .expect("corrupts runtime status");
    assert!(Runtimes::list(&conn).is_err());
    conn.execute("UPDATE runtimes SET status = 'unknown' WHERE id = 1", [])
        .expect("restores runtime status");
    conn.execute(
        "INSERT INTO runtimes (kind, endpoint, source, status) VALUES ('ollama', 'http://127.0.0.1:1', 'corrupt-source', 'unknown')",
        [],
    )
    .expect("inserts corrupt source");
    assert!(Runtimes::list(&conn).is_err());
    drop(conn);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn non_finite_progress_and_oversized_text_are_rejected() {
    let (database, dir) = test_db("bounds");
    let mut conn = open_conn(&database);
    let job = JobService::create_job(&mut conn, JobKind::RuntimeRefresh, "", "").expect("creates");
    assert!(matches!(
        JobService::record_progress(&mut conn, job.id, f64::NAN, "phase", ""),
        Err(JobError::InvalidArgument)
    ));
    assert!(matches!(
        JobService::record_progress(&mut conn, job.id, 0.5, &"p".repeat(97), ""),
        Err(JobError::InvalidArgument)
    ));
    assert!(matches!(
        JobService::create_job(&mut conn, JobKind::RuntimeRefresh, "", &"m".repeat(1025)),
        Err(JobError::InvalidArgument)
    ));
    drop(conn);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn normal_inserts_auto_enforce_bounded_event_retention() {
    let (database, dir) = test_db("auto-retention");
    let mut conn = open_conn(&database);
    let job = JobService::create_job(&mut conn, JobKind::RuntimeRefresh, "", "").expect("creates");
    let mut previous_id = 0i64;
    for index in 0..(MAX_RETAINED_EVENTS_PER_JOB + 7) {
        let id = JobEvents::insert(
            &conn,
            job.id,
            "PROGRESS",
            "phase",
            &format!("event-{index}"),
            0.1,
            None,
        )
        .expect("inserts");
        assert!(
            id > previous_id,
            "insert returns a strictly increasing real id"
        );
        previous_id = id;
    }
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM job_events WHERE job_id = ?1",
            [job.id],
            |row| row.get(0),
        )
        .expect("counts retained events");
    assert_eq!(count, MAX_RETAINED_EVENTS_PER_JOB);
    let oldest: i64 = conn
        .query_row(
            "SELECT MIN(id) FROM job_events WHERE job_id = ?1",
            [job.id],
            |row| row.get(0),
        )
        .expect("reads oldest id");
    assert!(
        oldest > 2,
        "oldest event advanced past the created+retained window"
    );
    let rows =
        JobEvents::list_for_job_stream(&conn, job.id, 0, MAX_EVENT_PAGE_SIZE).expect("stream rows");
    assert_eq!(rows.len(), MAX_EVENT_PAGE_SIZE);
    drop(conn);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn stream_error_sentinel_uses_byte_cap_not_character_cap() {
    let (database, dir) = test_db("multibyte-error");
    let mut conn = open_conn(&database);
    let job = JobService::create_job(&mut conn, JobKind::ModelPull, "", "").expect("creates");
    // 300 four-byte characters encode to 1200 bytes, above the 1024-byte cap
    // even though the character count (314) is below it.
    let error = serde_json::json!({ "message": "😀".repeat(300) });
    JobEvents::insert(&conn, job.id, "ERROR", "", "", 0.0, Some(&error))
        .expect("inserts multibyte error");
    let rows = JobEvents::list_for_job_stream(&conn, job.id, 0, 50).expect("rows");
    let projected: serde_json::Value = serde_json::from_str(
        rows.last()
            .expect("row")
            .error_json
            .as_deref()
            .expect("error"),
    )
    .expect("json");
    assert_eq!(projected["code"], "REDACTED_ERROR");
    drop(conn);
    database.close();
    let _ = std::fs::remove_dir_all(&dir);
}
