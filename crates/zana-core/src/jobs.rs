//! Persistent generic job lifecycle with legal transitions and events.

use rusqlite::Connection;
use serde_json::Value;

use crate::domain::{JobEventKind, JobKind, JobStatus};
use crate::error::CoreError;
use crate::repositories::{JobEventStreamRow, JobEvents, JobRow, Jobs, MAX_EVENT_PAGE_SIZE};

pub const MAX_RETAINED_EVENTS_PER_JOB: i64 = 2000;
pub const DEFAULT_EVENT_PAGE_SIZE: usize = 50;

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
        let job = Self::get_job(conn, job_id)?;
        require_transition(job.status, target).map_err(JobError::InvalidTransition)?;
        let phase = phase.unwrap_or(&job.phase);
        let message = message.unwrap_or(&job.message);
        let progress = progress_0_1.map(clamp_progress).unwrap_or(job.progress_0_1);
        let kind = if error.is_some() {
            JobEventKind::Error.as_str()
        } else {
            JobEventKind::StatusChanged.as_str()
        };
        let transaction = conn.transaction().map_err(|_| JobError::storage())?;
        Jobs::update(
            &transaction,
            job_id,
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
        let job = Self::get_job(conn, job_id)?;
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
        let transaction = conn.transaction().map_err(|_| JobError::storage())?;
        Jobs::update(
            &transaction,
            job_id,
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
        let job = Self::get_job(conn, job_id)?;
        if job.status.is_terminal() {
            return Err(JobError::InvalidTransition(InvalidJobTransition {
                current: job.status,
                target: job.status,
            }));
        }
        let progress = clamp_progress(progress_0_1);
        let message = if message.is_empty() {
            job.message.clone()
        } else {
            message.to_owned()
        };
        let transaction = conn.transaction().map_err(|_| JobError::storage())?;
        Jobs::update(
            &transaction,
            job_id,
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

pub fn clamp_progress(value: f64) -> f64 {
    if !value.is_finite() {
        0.0
    } else {
        value.clamp(0.0, 1.0)
    }
}

fn event_count(conn: &Connection, job_id: i64) -> Result<i64, CoreError> {
    conn.query_row(
        "SELECT COUNT(*) FROM job_events WHERE job_id = ?1",
        rusqlite::params![job_id],
        |row| row.get(0),
    )
    .map_err(|_| CoreError::database())
}
