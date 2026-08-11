//! Persistent generic job lifecycle with legal transitions and events.

use rusqlite::Connection;
use serde_json::Value;

use crate::domain::{JobEventKind, JobKind, JobStatus};
use crate::error::CoreError;
use crate::repositories::{
    JobEventStreamRow, JobEvents, JobRow, Jobs, MAX_EVENT_PAGE_SIZE,
    MAX_RETAINED_EVENTS_PER_JOB as MAX_RETAINED_EVENTS,
};

pub const DEFAULT_EVENT_PAGE_SIZE: usize = 50;
pub const MAX_MESSAGE_BYTES: usize = 1024;
pub const MAX_PHASE_BYTES: usize = 96;
pub const MAX_ERROR_BYTES: usize = 1024;
pub const MAX_RETAINED_EVENTS_PER_JOB: i64 = MAX_RETAINED_EVENTS;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InvalidJobTransition {
    pub current: JobStatus,
    pub target: JobStatus,
}

pub fn can_transition(current: JobStatus, target: JobStatus) -> bool {
    match current {
        JobStatus::Pending => matches!(
            target,
            JobStatus::Running | JobStatus::Failed | JobStatus::Cancelled
        ),
        JobStatus::Running => matches!(
            target,
            JobStatus::Succeeded | JobStatus::Failed | JobStatus::Cancelled
        ),
        JobStatus::Succeeded | JobStatus::Failed | JobStatus::Cancelled => false,
    }
}

pub fn require_transition(
    current: JobStatus,
    target: JobStatus,
) -> Result<(), InvalidJobTransition> {
    if can_transition(current, target) {
        Ok(())
    } else {
        Err(InvalidJobTransition { current, target })
    }
}

#[derive(Debug)]
pub enum JobError {
    NotFound,
    InvalidTransition(InvalidJobTransition),
    Storage,
    InvalidArgument,
}

impl JobError {
    fn storage() -> Self {
        Self::Storage
    }
}

pub struct JobService;

impl JobService {
    pub fn create_job(
        conn: &mut Connection,
        kind: JobKind,
        phase: &str,
        message: &str,
    ) -> Result<JobRow, JobError> {
        validate_service_text(phase, message, None)?;
        let transaction = conn.transaction().map_err(|_| JobError::storage())?;
        let id =
            Jobs::insert(&transaction, kind, phase, message).map_err(|_| JobError::storage())?;
        JobEvents::insert(
            &transaction,
            id,
            JobEventKind::Created.as_str(),
            phase,
            message,
            0.0,
            None,
        )
        .map_err(|_| JobError::storage())?;
        transaction.commit().map_err(|_| JobError::storage())?;
        Self::get_job(conn, id)
    }

    pub fn get_job(conn: &Connection, job_id: i64) -> Result<JobRow, JobError> {
        if job_id < 0 {
            return Err(JobError::InvalidArgument);
        }
        Jobs::get(conn, job_id)
            .map_err(|_| JobError::storage())?
            .ok_or(JobError::NotFound)
    }

    pub fn transition_job(
        conn: &mut Connection,
        job_id: i64,
        target: JobStatus,
        phase: Option<&str>,
        message: Option<&str>,
        progress_0_1: Option<f64>,
        error: Option<&Value>,
    ) -> Result<JobRow, JobError> {
        validate_service_text(phase.unwrap_or(""), message.unwrap_or(""), error)?;
        // Read the current status inside the write transaction and update
        // with an exact status predicate so two writers cannot both transition
        // from the same old state into conflicting terminal states.
        let transaction = conn.transaction().map_err(|_| JobError::storage())?;
        let current = Jobs::get(&transaction, job_id)
            .map_err(|_| JobError::storage())?
            .ok_or(JobError::NotFound)?;
        require_transition(current.status, target).map_err(JobError::InvalidTransition)?;
        let phase = phase.unwrap_or(&current.phase);
        let message = message.unwrap_or(&current.message);
        let progress = progress_0_1
            .map(validate_progress)
            .transpose()
            .map_err(|_| JobError::InvalidArgument)?
            .unwrap_or(current.progress_0_1);
        let kind = if error.is_some() {
            JobEventKind::Error.as_str()
        } else {
            JobEventKind::StatusChanged.as_str()
        };
        Jobs::update(
            &transaction,
            job_id,
            current.status,
            target,
            phase,
            message,
            progress,
            error,
        )
        .map_err(|_| JobError::storage())?;
        JobEvents::insert(&transaction, job_id, kind, phase, message, progress, error)
            .map_err(|_| JobError::storage())?;
        transaction.commit().map_err(|_| JobError::storage())?;
        Self::get_job(conn, job_id)
    }

    pub fn cancel_job(
        conn: &mut Connection,
        job_id: i64,
        reason: &str,
    ) -> Result<JobRow, JobError> {
        validate_service_text("", reason, None)?;
        let transaction = conn.transaction().map_err(|_| JobError::storage())?;
        let job = Jobs::get(&transaction, job_id)
            .map_err(|_| JobError::storage())?
            .ok_or(JobError::NotFound)?;
        if job.status.is_terminal() {
            return Err(JobError::InvalidTransition(InvalidJobTransition {
                current: job.status,
                target: JobStatus::Cancelled,
            }));
        }
        let message = if reason.is_empty() {
            job.message.clone()
        } else {
            reason.to_owned()
        };
        Jobs::update(
            &transaction,
            job_id,
            job.status,
            JobStatus::Cancelled,
            &job.phase,
            &message,
            job.progress_0_1,
            None,
        )
        .map_err(|_| JobError::storage())?;
        JobEvents::insert(
            &transaction,
            job_id,
            JobEventKind::Cancelled.as_str(),
            &job.phase,
            reason,
            job.progress_0_1,
            None,
        )
        .map_err(|_| JobError::storage())?;
        transaction.commit().map_err(|_| JobError::storage())?;
        Self::get_job(conn, job_id)
    }

    pub fn record_progress(
        conn: &mut Connection,
        job_id: i64,
        progress_0_1: f64,
        phase: &str,
        message: &str,
    ) -> Result<JobRow, JobError> {
        validate_service_text(phase, message, None)?;
        let transaction = conn.transaction().map_err(|_| JobError::storage())?;
        let job = Jobs::get(&transaction, job_id)
            .map_err(|_| JobError::storage())?
            .ok_or(JobError::NotFound)?;
        if job.status.is_terminal() {
            return Err(JobError::InvalidTransition(InvalidJobTransition {
                current: job.status,
                target: job.status,
            }));
        }
        let progress = validate_progress(progress_0_1).map_err(|_| JobError::InvalidArgument)?;
        let message = if message.is_empty() {
            job.message.clone()
        } else {
            message.to_owned()
        };
        Jobs::update(
            &transaction,
            job_id,
            job.status,
            job.status,
            phase,
            &message,
            progress,
            None,
        )
        .map_err(|_| JobError::storage())?;
        JobEvents::insert(
            &transaction,
            job_id,
            JobEventKind::Progress.as_str(),
            phase,
            &message,
            progress,
            None,
        )
        .map_err(|_| JobError::storage())?;
        transaction.commit().map_err(|_| JobError::storage())?;
        Self::get_job(conn, job_id)
    }

    pub fn list_events(
        conn: &Connection,
        job_id: i64,
        after_event_id: i64,
        limit: usize,
    ) -> Result<Vec<JobEventStreamRow>, JobError> {
        Self::validate_page(job_id, after_event_id, limit)?;
        Self::get_job(conn, job_id)?;
        JobEvents::list_for_job_stream(conn, job_id, after_event_id, limit)
            .map_err(|_| JobError::storage())
    }

    pub fn validate_page(job_id: i64, after_event_id: i64, limit: usize) -> Result<(), JobError> {
        if job_id < 0 || after_event_id < 0 || !(1..=MAX_EVENT_PAGE_SIZE).contains(&limit) {
            return Err(JobError::InvalidArgument);
        }
        Ok(())
    }

    /// Bound per-job retained events by deleting the oldest rows beyond the
    /// configured cap. Returns the number of rows removed.
    pub fn enforce_event_retention(
        conn: &Connection,
        job_id: i64,
        retain_count: i64,
    ) -> Result<i64, JobError> {
        if job_id < 0 || retain_count < 1 {
            return Err(JobError::InvalidArgument);
        }
        let keep_from = JobEvents::oldest_id_after_trim(conn, job_id, retain_count)
            .map_err(|_| JobError::storage())?;
        if keep_from == 0 {
            return Ok(0);
        }
        let before = event_count(conn, job_id).map_err(|_| JobError::storage())?;
        JobEvents::delete_before_id(conn, job_id, keep_from).map_err(|_| JobError::storage())?;
        Ok((before - retain_count).max(0))
    }

    /// Mark stale PENDING/RUNNING model pulls interrupted on restart; never
    /// auto-resumes. Returns the number of jobs recovered.
    pub fn recover_interrupted_model_pulls(conn: &mut Connection) -> Result<i64, JobError> {
        let active = Jobs::list_active(conn).map_err(|_| JobError::storage())?;
        let mut recovered = 0;
        for job in active {
            if job.kind != JobKind::ModelPull {
                continue;
            }
            let error = serde_json::json!({
                "code": "INTERRUPTED_ON_RESTART",
                "message": "Model acquisition was interrupted by a restart.",
                "recoverable": true,
                "actions": ["retry_pull"],
            });
            match Self::transition_job(
                conn,
                job.id,
                JobStatus::Failed,
                Some("interrupted"),
                Some("Model acquisition was interrupted by a restart."),
                None,
                Some(&error),
            ) {
                Ok(_) => recovered += 1,
                Err(JobError::InvalidTransition(_)) => {}
                Err(_) => return Err(JobError::storage()),
            }
        }
        Ok(recovered)
    }
}

fn validate_progress(value: f64) -> Result<f64, ()> {
    if !value.is_finite() {
        return Err(());
    }
    Ok(value.clamp(0.0, 1.0))
}

fn validate_service_text(
    phase: &str,
    message: &str,
    error: Option<&Value>,
) -> Result<(), JobError> {
    if phase.len() > MAX_PHASE_BYTES {
        return Err(JobError::InvalidArgument);
    }
    if message.len() > MAX_MESSAGE_BYTES {
        return Err(JobError::InvalidArgument);
    }
    if let Some(error) = error {
        let encoded = serde_json::to_vec(error).map_err(|_| JobError::InvalidArgument)?;
        if encoded.len() > MAX_ERROR_BYTES {
            return Err(JobError::InvalidArgument);
        }
    }
    Ok(())
}

fn event_count(conn: &Connection, job_id: i64) -> Result<i64, CoreError> {
    conn.query_row(
        "SELECT COUNT(*) FROM job_events WHERE job_id = ?1",
        rusqlite::params![job_id],
        |row| row.get(0),
    )
    .map_err(|_| CoreError::database())
}
