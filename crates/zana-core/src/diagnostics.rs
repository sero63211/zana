//! Bounded sequential System Doctor probes and deterministic reports.

use std::io::{self, Write};
use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::Value;

use crate::resources::capture_host_snapshot;
use crate::time::now_iso;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum Severity {
    Info,
    Warn,
    Error,
}

impl Severity {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Info => "info",
            Self::Warn => "warn",
            Self::Error => "error",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum CheckStatus {
    Pass,
    Warn,
    Fail,
    Unavailable,
    Skipped,
}

impl CheckStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pass => "pass",
            Self::Warn => "warn",
            Self::Fail => "fail",
            Self::Unavailable => "unavailable",
            Self::Skipped => "skipped",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum AggregateHealth {
    Healthy,
    PassWithLimitedFeatures,
    Failed,
}

impl AggregateHealth {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Healthy => "healthy",
            Self::PassWithLimitedFeatures => "pass_with_limited_features",
            Self::Failed => "failed",
        }
    }
}

#[derive(Debug, Clone)]
pub struct ProbeBudget {
    pub max_checks: usize,
    pub per_check_timeout_seconds: f64,
    pub total_budget_seconds: f64,
    pub max_output_chars: usize,
    pub max_path_count: usize,
    pub max_error_count: usize,
}

impl Default for ProbeBudget {
    fn default() -> Self {
        Self {
            max_checks: 64,
            per_check_timeout_seconds: 1.0,
            total_budget_seconds: 8.0,
            max_output_chars: 2_000,
            max_path_count: 32,
            max_error_count: 64,
        }
    }
}

impl ProbeBudget {
    pub fn validate(&self) -> Result<(), String> {
        if !(1..=64).contains(&self.max_checks)
            || !self.per_check_timeout_seconds.is_finite()
            || !(0.05..=5.0).contains(&self.per_check_timeout_seconds)
            || !self.total_budget_seconds.is_finite()
            || !(0.05..=30.0).contains(&self.total_budget_seconds)
            || !(256..=8192).contains(&self.max_output_chars)
            || !(1..=64).contains(&self.max_path_count)
            || !(1..=128).contains(&self.max_error_count)
        {
            return Err("probe budget values are out of range".to_owned());
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct Evidence {
    pub observed_source: String,
    pub value: Option<Value>,
    pub basename: Option<String>,
    pub digest_prefix: Option<String>,
    pub boolean_presence: Option<bool>,
    pub notes: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct RecoveryAction {
    pub code: String,
    pub message: String,
    pub optional: bool,
}

#[derive(Debug, Clone)]
pub struct DiagnosticIssue {
    pub code: String,
    pub severity: Severity,
    pub message: String,
    pub recovery_actions: Vec<RecoveryAction>,
}

#[derive(Debug, Clone)]
pub struct FeatureReadiness {
    pub feature: String,
    pub ready: bool,
    pub blocks_core_start: bool,
    pub blocks_feature_only: bool,
    pub missing_reason: String,
}

#[derive(Debug, Clone)]
pub struct DiagnosticCheck {
    pub check_id: String,
    pub name: String,
    pub status: CheckStatus,
    pub severity: Severity,
    pub duration_seconds: f64,
    pub observed_source: String,
    pub observed_at_iso: String,
    pub evidence: Evidence,
    pub issues: Vec<DiagnosticIssue>,
    pub feature_readiness: Vec<FeatureReadiness>,
}

#[derive(Debug, Clone)]
pub struct DiagnosticReport {
    pub generated_at_iso: String,
    pub budget: ProbeBudget,
    pub checks: Vec<DiagnosticCheck>,
    pub aggregate_health: AggregateHealth,
    pub total_duration_seconds: f64,
    pub skipped_or_unavailable_count: usize,
    pub error_count: usize,
    pub details: Value,
}

pub trait DiagnosticProbe: Send + Sync {
    fn check_id(&self) -> &'static str;
    fn name(&self) -> &'static str;
    fn run(&self, budget: &ProbeBudget) -> DiagnosticCheck;
}

pub struct PlatformProbe {
    pub data_root: String,
}

impl DiagnosticProbe for PlatformProbe {
    fn check_id(&self) -> &'static str {
        "platform"
    }

    fn name(&self) -> &'static str {
        "OS/arch/Rust and application paths"
    }

    fn run(&self, _budget: &ProbeBudget) -> DiagnosticCheck {
        DiagnosticCheck {
            check_id: self.check_id().to_owned(),
            name: self.name().to_owned(),
            status: CheckStatus::Pass,
            severity: Severity::Info,
            duration_seconds: 0.0,
            observed_source: "platform".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "platform".to_owned(),
                value: Some(Value::String(format!(
                    "{}/{}",
                    std::env::consts::OS,
                    std::env::consts::ARCH
                ))),
                basename: Some(crate::resources::redact_path(&self.data_root)),
                digest_prefix: None,
                boolean_presence: Some(!self.data_root.is_empty()),
                notes: vec!["Rust".to_owned(), env!("CARGO_PKG_VERSION").to_owned()],
            },
            issues: Vec::new(),
            feature_readiness: Vec::new(),
        }
    }
}

pub struct MemoryDiskProbe {
    pub min_available_memory_bytes: i64,
    pub min_free_disk_bytes: i64,
    pub path: String,
}

impl DiagnosticProbe for MemoryDiskProbe {
    fn check_id(&self) -> &'static str {
        "memory-disk"
    }

    fn name(&self) -> &'static str {
        "Memory and disk headroom"
    }

    fn run(&self, _budget: &ProbeBudget) -> DiagnosticCheck {
        let snapshot = capture_host_snapshot(&self.path, 0);
        let available = snapshot.memory_available_bytes;
        let free = snapshot.disk_free_bytes;
        let mut issues = Vec::new();
        let mut status = CheckStatus::Pass;
        let mut severity = Severity::Info;
        if available.is_none() || free.is_none() {
            status = CheckStatus::Unavailable;
            severity = Severity::Warn;
            issues.push(DiagnosticIssue {
                code: "HEADROOM_UNKNOWN".to_owned(),
                severity: Severity::Warn,
                message:
                    "Memory or disk headroom could not be measured; heavy work cannot be proven safe."
                        .to_owned(),
                recovery_actions: vec![RecoveryAction {
                    code: "CHECK_RESOURCE_SNAPSHOT".to_owned(),
                    message: "Refresh the resource snapshot and retry the doctor.".to_owned(),
                    optional: false,
                }],
            });
        }
        if available.is_some_and(|value| value < self.min_available_memory_bytes) {
            issues.push(DiagnosticIssue {
                code: "LOW_MEMORY".to_owned(),
                severity: Severity::Warn,
                message: "Available memory is below the recommended minimum.".to_owned(),
                recovery_actions: vec![RecoveryAction {
                    code: "CLOSE_HEAVY_APPS".to_owned(),
                    message: "Close memory-heavy applications before builds or training."
                        .to_owned(),
                    optional: false,
                }],
            });
            if status != CheckStatus::Unavailable {
                status = CheckStatus::Warn;
                severity = Severity::Warn;
            }
        }
        if free.is_some_and(|value| value < self.min_free_disk_bytes) {
            issues.push(DiagnosticIssue {
                code: "LOW_DISK".to_owned(),
                severity: Severity::Warn,
                message: "Free disk space is below the recommended minimum.".to_owned(),
                recovery_actions: vec![RecoveryAction {
                    code: "FREE_DISK".to_owned(),
                    message: "Free disk space before model or build acquisition.".to_owned(),
                    optional: false,
                }],
            });
            if status != CheckStatus::Unavailable {
                status = CheckStatus::Warn;
                severity = Severity::Warn;
            }
        }
        DiagnosticCheck {
            check_id: self.check_id().to_owned(),
            name: self.name().to_owned(),
            status,
            severity,
            duration_seconds: 0.0,
            observed_source: "libc/std".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "libc/std".to_owned(),
                value: free.map(Value::from),
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(available.is_some() && free.is_some()),
                notes: vec![
                    "available_bytes".to_owned(),
                    available.map_or_else(|| "unavailable".to_owned(), |value| value.to_string()),
                ],
            },
            issues,
            feature_readiness: vec![FeatureReadiness {
                feature: "memory_disk_headroom".to_owned(),
                ready: available.is_some() && free.is_some(),
                blocks_core_start: false,
                blocks_feature_only: true,
                missing_reason: if available.is_none() || free.is_none() {
                    "Memory or disk headroom is unavailable.".to_owned()
                } else {
                    String::new()
                },
            }],
        }
    }
}

pub struct SqliteReachabilityProbe {
    pub checker: Box<dyn Fn() -> Result<(String, i64), String> + Send + Sync>,
}

impl DiagnosticProbe for SqliteReachabilityProbe {
    fn check_id(&self) -> &'static str {
        "sqlite"
    }

    fn name(&self) -> &'static str {
        "SQLite reachability and pragmas"
    }

    fn run(&self, _budget: &ProbeBudget) -> DiagnosticCheck {
        let result = (self.checker)();
        let (ok, issue) = match result {
            Ok((journal_mode, foreign_keys)) => {
                let ok = journal_mode.eq_ignore_ascii_case("wal") && foreign_keys == 1;
                let issue = if ok {
                    None
                } else {
                    Some((
                        "SQLITE_PRAGMA_MISMATCH",
                        "SQLite is not using the required WAL/foreign-key settings.",
                    ))
                };
                (ok, issue)
            }
            Err(_) => (
                false,
                Some((
                    "SQLITE_UNREACHABLE",
                    "SQLite could not be reached for a read-only diagnostic.",
                )),
            ),
        };
        let issues = if let Some((code, message)) = issue {
            vec![DiagnosticIssue {
                code: code.to_owned(),
                severity: Severity::Error,
                message: message.to_owned(),
                recovery_actions: vec![RecoveryAction {
                    code: "RESTART_CORE".to_owned(),
                    message: "Restart ZANA Core to reopen the local database.".to_owned(),
                    optional: false,
                }],
            }]
        } else {
            Vec::new()
        };
        DiagnosticCheck {
            check_id: self.check_id().to_owned(),
            name: self.name().to_owned(),
            status: if ok {
                CheckStatus::Pass
            } else {
                CheckStatus::Fail
            },
            severity: if ok { Severity::Info } else { Severity::Error },
            duration_seconds: 0.0,
            observed_source: "rusqlite".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "rusqlite".to_owned(),
                value: None,
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(ok),
                notes: vec!["wal".to_owned()],
            },
            issues,
            feature_readiness: Vec::new(),
        }
    }
}

pub struct StorageRootProbe {
    pub roots: Vec<PathBuf>,
}

impl DiagnosticProbe for StorageRootProbe {
    fn check_id(&self) -> &'static str {
        "storage-roots"
    }

    fn name(&self) -> &'static str {
        "Artifact and image store roots"
    }

    fn run(&self, budget: &ProbeBudget) -> DiagnosticCheck {
        if self.roots.len() > budget.max_path_count {
            return self.failed("too many storage roots");
        }
        let mut issues = Vec::new();
        let mut observed = Vec::new();
        for root in &self.roots {
            let name = crate::resources::redact_path(&root.to_string_lossy());
            match std::fs::metadata(root) {
                Ok(metadata) if !metadata.is_dir() => issues.push(DiagnosticIssue {
                    code: "STORAGE_ROOT_NOT_DIRECTORY".to_owned(),
                    severity: Severity::Error,
                    message: "A storage root is not a directory.".to_owned(),
                    recovery_actions: vec![RecoveryAction {
                        code: "FIX_STORAGE_PERMISSIONS".to_owned(),
                        message: "Repair the storage root and retry.".to_owned(),
                        optional: false,
                    }],
                }),
                Ok(_) => observed.push(name),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    issues.push(DiagnosticIssue {
                        code: "STORAGE_ROOT_MISSING".to_owned(),
                        severity: Severity::Warn,
                        message: "A storage root does not exist yet.".to_owned(),
                        recovery_actions: vec![RecoveryAction {
                            code: "CREATE_STORAGE_ROOT".to_owned(),
                            message: "ZANA will create the root when first used.".to_owned(),
                            optional: true,
                        }],
                    });
                }
                Err(_) => issues.push(DiagnosticIssue {
                    code: "STORAGE_ROOT_UNREADABLE".to_owned(),
                    severity: Severity::Error,
                    message: "A storage root could not be inspected.".to_owned(),
                    recovery_actions: vec![RecoveryAction {
                        code: "REPAIR_STORAGE_ROOT".to_owned(),
                        message: "Repair the storage root path and retry.".to_owned(),
                        optional: false,
                    }],
                }),
            }
        }
        let has_error = issues.iter().any(|issue| issue.severity == Severity::Error);
        let status = if has_error {
            CheckStatus::Fail
        } else if !issues.is_empty() {
            CheckStatus::Warn
        } else {
            CheckStatus::Pass
        };
        DiagnosticCheck {
            check_id: self.check_id().to_owned(),
            name: self.name().to_owned(),
            status,
            severity: if status == CheckStatus::Pass {
                Severity::Info
            } else {
                Severity::Warn
            },
            duration_seconds: 0.0,
            observed_source: "std::fs".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "std::fs".to_owned(),
                value: None,
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(issues.is_empty()),
                notes: observed.into_iter().take(4).collect(),
            },
            issues,
            feature_readiness: Vec::new(),
        }
    }
}

impl StorageRootProbe {
    fn failed(&self, message: &str) -> DiagnosticCheck {
        DiagnosticCheck {
            check_id: self.check_id().to_owned(),
            name: self.name().to_owned(),
            status: CheckStatus::Fail,
            severity: Severity::Error,
            duration_seconds: 0.0,
            observed_source: "std::fs".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "std::fs".to_owned(),
                value: None,
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(false),
                notes: vec![message.to_owned()],
            },
            issues: vec![DiagnosticIssue {
                code: "STORAGE_PATH_BUDGET".to_owned(),
                severity: Severity::Error,
                message: message.to_owned(),
                recovery_actions: vec![RecoveryAction {
                    code: "REDUCE_STORAGE_ROOTS".to_owned(),
                    message: "Configure fewer storage roots.".to_owned(),
                    optional: false,
                }],
            }],
            feature_readiness: Vec::new(),
        }
    }
}

pub struct RuntimeDiscoveryProbe {
    pub state: Box<dyn Fn() -> (Vec<String>, Vec<String>) + Send + Sync>,
}

impl DiagnosticProbe for RuntimeDiscoveryProbe {
    fn check_id(&self) -> &'static str {
        "runtimes"
    }

    fn name(&self) -> &'static str {
        "Available runtime endpoints"
    }

    fn run(&self, _budget: &ProbeBudget) -> DiagnosticCheck {
        let (online, installed_not_running) = (self.state)();
        let mut notes: Vec<String> = online.iter().take(4).cloned().collect();
        for name in installed_not_running.iter().take(2) {
            notes.push(format!("{name}:installed-not-running"));
        }
        let issues = if online.is_empty() {
            vec![DiagnosticIssue {
                code: "NO_RUNTIME_ONLINE".to_owned(),
                severity: Severity::Warn,
                message: "No supported local runtime endpoint is currently online.".to_owned(),
                recovery_actions: vec![RecoveryAction {
                    code: "START_RUNTIME_MANUALLY".to_owned(),
                    message: "Start your local runtime or add a manual endpoint.".to_owned(),
                    optional: true,
                }],
            }]
        } else {
            Vec::new()
        };
        DiagnosticCheck {
            check_id: self.check_id().to_owned(),
            name: self.name().to_owned(),
            status: if online.is_empty() {
                CheckStatus::Warn
            } else {
                CheckStatus::Pass
            },
            severity: if online.is_empty() {
                Severity::Warn
            } else {
                Severity::Info
            },
            duration_seconds: 0.0,
            observed_source: "runtime-registry".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "runtime-registry".to_owned(),
                value: Some(Value::from(online.len() as u64)),
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(!online.is_empty()),
                notes,
            },
            issues,
            feature_readiness: vec![FeatureReadiness {
                feature: "runtime_discovery".to_owned(),
                ready: !online.is_empty(),
                blocks_core_start: false,
                blocks_feature_only: true,
                missing_reason: if online.is_empty() {
                    "No local runtime is online.".to_owned()
                } else {
                    String::new()
                },
            }],
        }
    }
}

pub struct OptionalFeatureProbe {
    pub features: Vec<FeatureReadiness>,
}

impl DiagnosticProbe for OptionalFeatureProbe {
    fn check_id(&self) -> &'static str {
        "optional-features"
    }

    fn name(&self) -> &'static str {
        "Optional Rust/runtime feature readiness"
    }

    fn run(&self, budget: &ProbeBudget) -> DiagnosticCheck {
        let feature_cap = budget.max_path_count.min(16);
        let features: Vec<FeatureReadiness> = self
            .features
            .iter()
            .take(feature_cap)
            .map(|feature| FeatureReadiness {
                feature: truncate_str(feature.feature.as_str(), 128, budget.max_output_chars),
                ready: feature.ready,
                blocks_core_start: feature.blocks_core_start,
                blocks_feature_only: feature.blocks_feature_only,
                missing_reason: truncate_str(
                    feature.missing_reason.as_str(),
                    256,
                    budget.max_output_chars,
                ),
            })
            .collect();
        let ready_count = features.iter().filter(|feature| feature.ready).count();
        let missing = features.iter().filter(|feature| !feature.ready).count();
        let truncated = self.features.len() > feature_cap;
        let all_ready = missing == 0 && !truncated;
        let issues = if truncated {
            vec![DiagnosticIssue {
                code: "FEATURE_LIST_TRUNCATED".to_owned(),
                severity: Severity::Warn,
                message: "The optional feature list exceeded the probe cap.".to_owned(),
                recovery_actions: vec![RecoveryAction {
                    code: "REDUCE_FEATURES".to_owned(),
                    message: "Configure fewer optional features.".to_owned(),
                    optional: true,
                }],
            }]
        } else if all_ready {
            Vec::new()
        } else {
            vec![DiagnosticIssue {
                code: "OPTIONAL_FEATURES_LIMITED".to_owned(),
                severity: Severity::Warn,
                message: "Some optional features are not ready; those features are limited."
                    .to_owned(),
                recovery_actions: vec![RecoveryAction {
                    code: "ENABLE_FEATURE".to_owned(),
                    message: "Enable or configure the missing optional feature.".to_owned(),
                    optional: true,
                }],
            }]
        };
        DiagnosticCheck {
            check_id: self.check_id().to_owned(),
            name: self.name().to_owned(),
            status: if all_ready {
                CheckStatus::Pass
            } else if truncated {
                CheckStatus::Unavailable
            } else {
                CheckStatus::Warn
            },
            severity: if all_ready {
                Severity::Info
            } else {
                Severity::Warn
            },
            duration_seconds: 0.0,
            observed_source: "injected-feature-state".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "injected-feature-state".to_owned(),
                value: Some(Value::from(ready_count as u64)),
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(all_ready),
                notes: features
                    .iter()
                    .map(|feature| {
                        format!(
                            "{}:{}",
                            feature.feature,
                            if feature.ready { "ready" } else { "limited" }
                        )
                    })
                    .collect(),
            },
            issues,
            feature_readiness: features,
        }
    }
}

pub struct LoopbackAuthProbe {
    pub token_present: bool,
}

impl DiagnosticProbe for LoopbackAuthProbe {
    fn check_id(&self) -> &'static str {
        "loopback-auth"
    }

    fn name(&self) -> &'static str {
        "Loopback bearer authentication"
    }

    fn run(&self, _budget: &ProbeBudget) -> DiagnosticCheck {
        let issues = if self.token_present {
            Vec::new()
        } else {
            vec![DiagnosticIssue {
                code: "AUTH_TOKEN_MISSING".to_owned(),
                severity: Severity::Error,
                message: "No per-launch bearer token is configured.".to_owned(),
                recovery_actions: vec![RecoveryAction {
                    code: "PROVIDE_LAUNCH_TOKEN".to_owned(),
                    message: "Start ZANA Core with --token or ZANA_CORE_TOKEN.".to_owned(),
                    optional: false,
                }],
            }]
        };
        DiagnosticCheck {
            check_id: self.check_id().to_owned(),
            name: self.name().to_owned(),
            status: if self.token_present {
                CheckStatus::Pass
            } else {
                CheckStatus::Fail
            },
            severity: if self.token_present {
                Severity::Info
            } else {
                Severity::Error
            },
            duration_seconds: 0.0,
            observed_source: "auth".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "auth".to_owned(),
                value: None,
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(self.token_present),
                notes: vec!["per-launch token".to_owned()],
            },
            issues,
            feature_readiness: Vec::new(),
        }
    }
}

pub struct DoctorService {
    pub budget: ProbeBudget,
}

const PROBE_WORKERS: usize = 2;
const PROBE_QUEUE_DEPTH: usize = 4;

enum ProbeJob {
    Run(
        Box<dyn DiagnosticProbe>,
        ProbeBudget,
        SyncSender<ProbeOutcome>,
    ),
}

enum ProbeOutcome {
    Check(Box<DiagnosticCheck>),
    Panicked,
}

struct ProbeExecutor {
    sender: SyncSender<ProbeJob>,
    permits: Arc<Mutex<usize>>,
}

struct PermitGuard {
    permits: Arc<Mutex<usize>>,
    released: bool,
}

impl Drop for PermitGuard {
    fn drop(&mut self) {
        if !self.released {
            *lock(&self.permits) += 1;
            self.released = true;
        }
    }
}

impl ProbeExecutor {
    fn run(
        &self,
        probe: Box<dyn DiagnosticProbe>,
        budget: ProbeBudget,
        effective_timeout: Duration,
    ) -> Result<ProbeOutcome, String> {
        let (sender, receiver) = mpsc::sync_channel(1);
        let mut permits = lock(&self.permits);
        if *permits == 0 {
            return Err("probe worker pool is busy".to_owned());
        }
        *permits -= 1;
        drop(permits);
        let job = ProbeJob::Run(probe, budget, sender);
        match self.sender.try_send(job) {
            Ok(()) => {}
            Err(_) => {
                *lock(&self.permits) += 1;
                return Err("probe worker pool is busy or shutting down".to_owned());
            }
        }
        match receiver.recv_timeout(effective_timeout) {
            Ok(outcome) => Ok(outcome),
            // The worker still owns its permit; it releases exactly once after
            // the probe finishes, even though this caller already timed out.
            Err(mpsc::RecvTimeoutError::Timeout) => {
                Err("probe exceeded its per-check time budget".to_owned())
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                Err("probe worker disconnected".to_owned())
            }
        }
    }

    fn worker_loop(receiver: Arc<Mutex<Receiver<ProbeJob>>>, permits: Arc<Mutex<usize>>) {
        loop {
            let job = {
                let guard = lock(&receiver);
                match guard.recv() {
                    Ok(job) => job,
                    Err(_) => return,
                }
            };
            match job {
                ProbeJob::Run(probe, budget, sender) => {
                    let guard = PermitGuard {
                        permits: Arc::clone(&permits),
                        released: false,
                    };
                    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        probe.run(&budget)
                    }));
                    let _ = sender.send(match outcome {
                        Ok(check) => ProbeOutcome::Check(Box::new(check)),
                        Err(_) => ProbeOutcome::Panicked,
                    });
                    drop(guard);
                }
            }
        }
    }
}

#[cfg(test)]
fn local_executor() -> Result<ProbeExecutor, String> {
    let (sender, receiver) = mpsc::sync_channel(PROBE_QUEUE_DEPTH);
    let permits = Arc::new(Mutex::new(PROBE_WORKERS));
    let receiver = Arc::new(Mutex::new(receiver));
    for _ in 0..PROBE_WORKERS {
        let receiver = Arc::clone(&receiver);
        let permits = Arc::clone(&permits);
        std::thread::Builder::new()
            .name("zana-doctor-probe-test".to_owned())
            .spawn(move || {
                ProbeExecutor::worker_loop(receiver, permits);
            })
            .map_err(|_| "doctor probe workers could not be started".to_owned())?;
    }
    Ok(ProbeExecutor { sender, permits })
}

fn executor() -> Result<&'static ProbeExecutor, &'static str> {
    static EXECUTOR: OnceLock<Result<ProbeExecutor, String>> = OnceLock::new();
    EXECUTOR
        .get_or_init(|| {
            let (sender, receiver) = mpsc::sync_channel(PROBE_QUEUE_DEPTH);
            let permits = Arc::new(Mutex::new(PROBE_WORKERS));
            let receiver = Arc::new(Mutex::new(receiver));
            for _ in 0..PROBE_WORKERS {
                let receiver = Arc::clone(&receiver);
                let permits = Arc::clone(&permits);
                let result = std::thread::Builder::new()
                    .name("zana-doctor-probe".to_owned())
                    .spawn(move || {
                        ProbeExecutor::worker_loop(receiver, permits);
                    });
                if result.is_err() {
                    return Err("doctor probe workers could not be started".to_owned());
                }
            }
            Ok(ProbeExecutor { sender, permits })
        })
        .as_ref()
        .map_err(|_| "doctor probe workers are unavailable")
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    match mutex.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    }
}

impl DoctorService {
    pub fn run(&self, probes: Vec<Box<dyn DiagnosticProbe>>) -> Result<DiagnosticReport, String> {
        self.budget.validate()?;
        if probes.len() > self.budget.max_checks {
            return Err("check count exceeds the probe budget".to_owned());
        }
        let started = Instant::now();
        let mut checks = Vec::new();
        let executor = executor();
        let mut worker_busy = false;
        for probe in probes {
            let remaining = self.budget.total_budget_seconds - started.elapsed().as_secs_f64();
            if remaining <= 0.0 {
                checks.push(self.budget_check("total time budget exceeded"));
                break;
            }
            if worker_busy {
                checks.push(self.probe_failure(
                    probe.as_ref(),
                    0.0,
                    "probe worker pool was still busy from an earlier timeout",
                ));
                continue;
            }
            let per_check = self.budget.per_check_timeout_seconds.min(remaining);
            let probe_id = probe.check_id().to_owned();
            let probe_name = probe.name().to_owned();
            let result = match executor {
                Ok(executor) => executor.run(
                    probe,
                    self.budget.clone(),
                    Duration::from_secs_f64(per_check),
                ),
                Err(_) => {
                    worker_busy = true;
                    Err("doctor probe workers are unavailable".to_owned())
                }
            };
            let check = match result {
                Ok(ProbeOutcome::Check(check)) => *check,
                Ok(ProbeOutcome::Panicked) => self.probe_failure_named(
                    &probe_id,
                    &probe_name,
                    per_check,
                    "probe failed without crashing the report",
                ),
                Err(error) => {
                    worker_busy = true;
                    self.probe_failure_named(&probe_id, &probe_name, per_check, &error)
                }
            };
            checks.push(bound_check(check, &self.budget));
        }
        let total = started.elapsed().as_secs_f64();
        let error_count = checks
            .iter()
            .filter(|check| check.status == CheckStatus::Fail)
            .count();
        let skipped = checks
            .iter()
            .filter(|check| {
                matches!(
                    check.status,
                    CheckStatus::Unavailable | CheckStatus::Skipped
                )
            })
            .count();
        let mandatory_failure = checks.iter().any(|check| {
            check.status == CheckStatus::Fail
                || check
                    .issues
                    .iter()
                    .any(|issue| issue.severity == Severity::Error)
                || check
                    .feature_readiness
                    .iter()
                    .any(|readiness| !readiness.ready && readiness.blocks_core_start)
        });
        let limited = checks.iter().any(|check| {
            matches!(
                check.status,
                CheckStatus::Warn | CheckStatus::Unavailable | CheckStatus::Skipped
            ) || check
                .feature_readiness
                .iter()
                .any(|readiness| !readiness.ready)
        });
        let aggregate = if mandatory_failure {
            AggregateHealth::Failed
        } else if limited {
            AggregateHealth::PassWithLimitedFeatures
        } else {
            AggregateHealth::Healthy
        };
        let probe_count = checks.len();
        Ok(DiagnosticReport {
            generated_at_iso: now_iso(),
            budget: self.budget.clone(),
            checks,
            aggregate_health: aggregate,
            total_duration_seconds: (total * 1000.0).round() / 1000.0,
            skipped_or_unavailable_count: skipped,
            error_count,
            details: serde_json::json!({ "probe_count": probe_count }),
        })
    }

    fn probe_failure(
        &self,
        probe: &dyn DiagnosticProbe,
        duration: f64,
        note: &str,
    ) -> DiagnosticCheck {
        self.probe_failure_named(probe.check_id(), probe.name(), duration, note)
    }

    fn probe_failure_named(
        &self,
        check_id: &str,
        name: &str,
        duration: f64,
        note: &str,
    ) -> DiagnosticCheck {
        DiagnosticCheck {
            check_id: check_id.to_owned(),
            name: name.to_owned(),
            status: CheckStatus::Unavailable,
            severity: Severity::Warn,
            duration_seconds: (duration * 1000.0).round() / 1000.0,
            observed_source: "doctor".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "doctor".to_owned(),
                value: None,
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(false),
                notes: vec![note.to_owned()],
            },
            issues: vec![DiagnosticIssue {
                code: if note.contains("time budget") {
                    "PROBE_TIMEOUT".to_owned()
                } else {
                    "PROBE_FAILED".to_owned()
                },
                severity: Severity::Warn,
                message: "A diagnostic probe did not complete within its bounded budget."
                    .to_owned(),
                recovery_actions: vec![RecoveryAction {
                    code: "RETRY_DOCTOR".to_owned(),
                    message: "Run the doctor again after correcting the environment.".to_owned(),
                    optional: false,
                }],
            }],
            feature_readiness: Vec::new(),
        }
    }

    fn budget_check(&self, message: &str) -> DiagnosticCheck {
        DiagnosticCheck {
            check_id: "budget".to_owned(),
            name: "Diagnostic budget".to_owned(),
            status: CheckStatus::Skipped,
            severity: Severity::Warn,
            duration_seconds: 0.0,
            observed_source: "doctor".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "doctor".to_owned(),
                value: None,
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(false),
                notes: vec![message.to_owned()],
            },
            issues: Vec::new(),
            feature_readiness: Vec::new(),
        }
    }
}

fn bound_check(mut check: DiagnosticCheck, budget: &ProbeBudget) -> DiagnosticCheck {
    if !check.duration_seconds.is_finite() || check.duration_seconds < 0.0 {
        check.duration_seconds = 0.0;
    }
    let mut budget_remaining = budget.max_output_chars;
    let consume = |value: String, max: usize, remaining: &mut usize| -> String {
        let text = truncate_text(value, max, *remaining);
        *remaining = remaining.saturating_sub(text.len());
        text
    };
    check.check_id = consume(
        std::mem::take(&mut check.check_id),
        64,
        &mut budget_remaining,
    );
    check.name = consume(std::mem::take(&mut check.name), 128, &mut budget_remaining);
    check.observed_source = consume(
        std::mem::take(&mut check.observed_source),
        128,
        &mut budget_remaining,
    );
    check.observed_at_iso = consume(
        std::mem::take(&mut check.observed_at_iso),
        64,
        &mut budget_remaining,
    );
    check.evidence.observed_source = consume(
        std::mem::take(&mut check.evidence.observed_source),
        128,
        &mut budget_remaining,
    );
    check.evidence.value = match check.evidence.value {
        Some(value) => match bound_json_value(value, budget_remaining) {
            Some(bounded) => {
                let mut writer = LimitWriter {
                    remaining: budget_remaining,
                };
                let _ = serde_json::to_writer(&mut writer, &bounded);
                budget_remaining = writer.remaining;
                Some(bounded)
            }
            None => None,
        },
        None => None,
    };
    check.evidence.basename = check
        .evidence
        .basename
        .map(|value| consume(value, 128, &mut budget_remaining));
    check.evidence.digest_prefix = check
        .evidence
        .digest_prefix
        .map(|value| consume(value, 32, &mut budget_remaining));
    let mut notes = Vec::new();
    for value in check.evidence.notes.into_iter().take(8) {
        if budget_remaining == 0 {
            break;
        }
        notes.push(consume(value, 256, &mut budget_remaining));
    }
    check.evidence.notes = notes;
    let mut issues = Vec::new();
    for mut issue in check.issues.into_iter().take(budget.max_error_count) {
        if budget_remaining == 0 {
            break;
        }
        issue.code = consume(std::mem::take(&mut issue.code), 64, &mut budget_remaining);
        issue.message = consume(
            std::mem::take(&mut issue.message),
            512,
            &mut budget_remaining,
        );
        let mut actions = Vec::new();
        for action in issue.recovery_actions.into_iter().take(8) {
            if budget_remaining == 0 {
                break;
            }
            actions.push(RecoveryAction {
                code: consume(action.code, 64, &mut budget_remaining),
                message: consume(action.message, 512, &mut budget_remaining),
                optional: action.optional,
            });
        }
        issue.recovery_actions = actions;
        issues.push(issue);
    }
    check.issues = issues;
    let mut readiness = Vec::new();
    for item in check.feature_readiness.into_iter().take(8) {
        if budget_remaining == 0 {
            break;
        }
        readiness.push(FeatureReadiness {
            feature: consume(item.feature, 128, &mut budget_remaining),
            ready: item.ready,
            blocks_core_start: item.blocks_core_start,
            blocks_feature_only: item.blocks_feature_only,
            missing_reason: consume(item.missing_reason, 256, &mut budget_remaining),
        });
    }
    check.feature_readiness = readiness;
    check
}

fn bound_json_value(value: Value, max_output: usize) -> Option<Value> {
    if max_output == 0 || !json_tree_within(&value, 0, max_output) {
        return None;
    }
    let mut writer = LimitWriter {
        remaining: max_output,
    };
    if serde_json::to_writer(&mut writer, &value).is_ok() {
        Some(value)
    } else {
        None
    }
}

/// Cheap by-reference pre-walk that bounds depth and approximate tree size
/// before serde's exact serializer runs, avoiding deep recursion and
/// unbounded transient work.
fn json_tree_within(value: &Value, depth: usize, limit: usize) -> bool {
    if depth > 24 {
        return false;
    }
    let mut visited = 0usize;
    let mut stack = vec![(value, depth)];
    while let Some((item, current_depth)) = stack.pop() {
        visited += 1;
        if visited > limit {
            return false;
        }
        match item {
            Value::Object(map) => {
                if current_depth > 24 {
                    return false;
                }
                for (key, child) in map {
                    visited = visited.saturating_add(1).saturating_add(key.len());
                    if visited > limit {
                        return false;
                    }
                    stack.push((child, current_depth + 1));
                }
            }
            Value::Array(items) => {
                if current_depth > 24 {
                    return false;
                }
                for child in items {
                    visited += 1;
                    if visited > limit {
                        return false;
                    }
                    stack.push((child, current_depth + 1));
                }
            }
            Value::String(text) => {
                visited += text.len();
                if visited > limit {
                    return false;
                }
            }
            _ => {}
        }
    }
    true
}

struct LimitWriter {
    remaining: usize,
}

impl Write for LimitWriter {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        if buffer.len() > self.remaining {
            return Err(io::Error::new(
                io::ErrorKind::WriteZero,
                "output limit exceeded",
            ));
        }
        self.remaining -= buffer.len();
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn truncate_text(value: String, max_chars: usize, max_output_chars: usize) -> String {
    truncate_str(&value, max_chars, max_output_chars)
}

fn truncate_str(value: &str, max_chars: usize, max_output_chars: usize) -> String {
    let marker = "...[truncated]";
    let marker_bytes = marker.len();
    let max = max_chars.min(max_output_chars);
    if value.len() <= max {
        return value.to_owned();
    }
    if max == 0 {
        return String::new();
    }
    let budget = max.saturating_sub(marker_bytes);
    if budget == 0 {
        // Return only the bounded marker prefix when there is no room for the
        // full marker, so the result never exceeds the byte cap.
        return marker[..max.min(marker_bytes)].to_owned();
    }
    let bytes = value.as_bytes();
    let mut end = budget.min(bytes.len());
    while end > 0 && (bytes[end] & 0xC0) == 0x80 {
        end -= 1;
    }
    let mut result = String::with_capacity(end + marker_bytes.min(max - end));
    result.push_str(&value[..end]);
    let marker_room = max - end;
    result.push_str(&marker[..marker_room.min(marker_bytes)]);
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static DOCTOR_TEST_LOCK: Mutex<()> = Mutex::new(());

    struct NormalProbe;

    impl DiagnosticProbe for NormalProbe {
        fn check_id(&self) -> &'static str {
            "normal"
        }

        fn name(&self) -> &'static str {
            "Normal probe"
        }

        fn run(&self, _budget: &ProbeBudget) -> DiagnosticCheck {
            DiagnosticCheck {
                check_id: self.check_id().to_owned(),
                name: self.name().to_owned(),
                status: CheckStatus::Pass,
                severity: Severity::Info,
                duration_seconds: 0.0,
                observed_source: "normal".to_owned(),
                observed_at_iso: now_iso(),
                evidence: Evidence {
                    observed_source: "normal".to_owned(),
                    value: None,
                    basename: None,
                    digest_prefix: None,
                    boolean_presence: Some(true),
                    notes: Vec::new(),
                },
                issues: Vec::new(),
                feature_readiness: Vec::new(),
            }
        }
    }

    struct SlowProbe {
        millis: u64,
    }

    struct StaticProbe(DiagnosticCheck);

    impl DiagnosticProbe for StaticProbe {
        fn check_id(&self) -> &'static str {
            "static"
        }

        fn name(&self) -> &'static str {
            "Static probe"
        }

        fn run(&self, _budget: &ProbeBudget) -> DiagnosticCheck {
            self.0.clone()
        }
    }

    impl DiagnosticProbe for SlowProbe {
        fn check_id(&self) -> &'static str {
            "slow"
        }

        fn name(&self) -> &'static str {
            "Slow probe"
        }

        fn run(&self, _budget: &ProbeBudget) -> DiagnosticCheck {
            std::thread::sleep(Duration::from_millis(self.millis));
            DiagnosticCheck {
                check_id: self.check_id().to_owned(),
                name: self.name().to_owned(),
                status: CheckStatus::Pass,
                severity: Severity::Info,
                duration_seconds: 0.0,
                observed_source: "slow".to_owned(),
                observed_at_iso: now_iso(),
                evidence: Evidence {
                    observed_source: "slow".to_owned(),
                    value: None,
                    basename: None,
                    digest_prefix: None,
                    boolean_presence: Some(true),
                    notes: Vec::new(),
                },
                issues: Vec::new(),
                feature_readiness: Vec::new(),
            }
        }
    }

    struct PanicProbe;

    impl DiagnosticProbe for PanicProbe {
        fn check_id(&self) -> &'static str {
            "panic"
        }

        fn name(&self) -> &'static str {
            "Panic probe"
        }

        fn run(&self, _budget: &ProbeBudget) -> DiagnosticCheck {
            panic!("hostile probe");
        }
    }

    #[test]
    fn doctor_aggregates_failures_and_isolates_panics() {
        let _guard = DOCTOR_TEST_LOCK.lock().expect("locks doctor tests");
        let doctor = DoctorService {
            budget: ProbeBudget::default(),
        };
        let report = doctor
            .run(vec![
                Box::new(LoopbackAuthProbe {
                    token_present: false,
                }),
                Box::new(PanicProbe),
                Box::new(PlatformProbe {
                    data_root: "/tmp/zana-doctor".to_owned(),
                }),
            ])
            .expect("runs");
        assert_eq!(report.aggregate_health, AggregateHealth::Failed);
        assert_eq!(report.error_count, 1);
        assert_eq!(report.skipped_or_unavailable_count, 1);
        assert_eq!(report.checks[1].status, CheckStatus::Unavailable);
    }

    #[test]
    fn doctor_respects_check_budget() {
        let _guard = DOCTOR_TEST_LOCK.lock().expect("locks doctor tests");
        let doctor = DoctorService {
            budget: ProbeBudget {
                max_checks: 1,
                ..ProbeBudget::default()
            },
        };
        assert!(doctor
            .run(vec![
                Box::new(PlatformProbe {
                    data_root: String::new()
                }),
                Box::new(PlatformProbe {
                    data_root: String::new()
                }),
            ])
            .is_err());
    }

    #[test]
    fn doctor_timeout_keeps_worker_permit_until_finished_and_bounds_run() {
        let _guard = DOCTOR_TEST_LOCK.lock().expect("locks doctor tests");
        let doctor = DoctorService {
            budget: ProbeBudget {
                per_check_timeout_seconds: 0.05,
                total_budget_seconds: 2.0,
                ..ProbeBudget::default()
            },
        };
        let started = Instant::now();
        let report = doctor
            .run(vec![
                Box::new(SlowProbe { millis: 200 }),
                Box::new(NormalProbe),
                Box::new(NormalProbe),
            ])
            .expect("runs");
        let elapsed = started.elapsed().as_secs_f64();
        assert!(
            elapsed < 1.5,
            "run duration must stay bounded, got {elapsed}"
        );
        assert_eq!(report.checks[0].status, CheckStatus::Unavailable);
        assert_eq!(
            report.checks[1].status,
            CheckStatus::Unavailable,
            "later probes are not launched after a timeout"
        );
        assert_eq!(report.checks[2].status, CheckStatus::Unavailable);
        let executor = executor().expect("executor");
        assert_eq!(
            *executor.permits.lock().expect("locks"),
            1,
            "timed-out worker still owns its permit"
        );
        std::thread::sleep(Duration::from_millis(250));
        assert_eq!(
            *executor.permits.lock().expect("locks"),
            PROBE_WORKERS,
            "worker releases exactly once after finishing"
        );
        let normal = doctor
            .run(vec![Box::new(NormalProbe)])
            .expect("normal run succeeds");
        assert_eq!(normal.checks[0].status, CheckStatus::Pass);
    }

    #[test]
    fn doctor_executor_never_exceeds_fixed_worker_cap() {
        let executor = local_executor().expect("local executor");
        let budget = ProbeBudget {
            per_check_timeout_seconds: 0.05,
            ..ProbeBudget::default()
        };
        let first = executor.run(
            Box::new(SlowProbe { millis: 200 }),
            budget.clone(),
            Duration::from_millis(50),
        );
        assert!(first.is_err());
        let second = executor.run(
            Box::new(SlowProbe { millis: 200 }),
            budget.clone(),
            Duration::from_millis(50),
        );
        assert!(second.is_err());
        assert_eq!(
            *executor.permits.lock().expect("locks"),
            0,
            "both fixed workers are occupied"
        );
        let third = executor.run(Box::new(NormalProbe), budget, Duration::from_millis(50));
        assert!(third.is_err(), "no worker permit is available");
        std::thread::sleep(Duration::from_millis(450));
        assert_eq!(*executor.permits.lock().expect("locks"), PROBE_WORKERS);
        let recovered = executor.run(
            Box::new(NormalProbe),
            ProbeBudget::default(),
            Duration::from_secs(1),
        );
        assert!(matches!(recovered, Ok(ProbeOutcome::Check(_))));
    }

    #[test]
    fn doctor_aggregates_limited_for_unavailable_and_readiness() {
        let _guard = DOCTOR_TEST_LOCK.lock().expect("locks doctor tests");
        let doctor = DoctorService {
            budget: ProbeBudget::default(),
        };
        let unavailable = doctor.run(vec![Box::new(PanicProbe)]).expect("runs");
        assert_eq!(
            unavailable.aggregate_health,
            AggregateHealth::PassWithLimitedFeatures
        );
        let limited = doctor
            .run(vec![Box::new(OptionalFeatureProbe {
                features: vec![FeatureReadiness {
                    feature: "mlx_runtime".to_owned(),
                    ready: false,
                    blocks_core_start: false,
                    blocks_feature_only: true,
                    missing_reason: "MLX runtime is not configured".to_owned(),
                }],
            })])
            .expect("runs");
        assert_eq!(
            limited.aggregate_health,
            AggregateHealth::PassWithLimitedFeatures
        );
        assert_eq!(limited.checks[0].status, CheckStatus::Warn);
        let all_ready = doctor
            .run(vec![Box::new(OptionalFeatureProbe {
                features: vec![FeatureReadiness {
                    feature: "local_runtime".to_owned(),
                    ready: true,
                    blocks_core_start: false,
                    blocks_feature_only: true,
                    missing_reason: String::new(),
                }],
            })])
            .expect("runs");
        assert_eq!(all_ready.aggregate_health, AggregateHealth::Healthy);
    }

    #[test]
    fn aggregate_fails_closed_for_bare_fail_error_and_core_readiness() {
        let _guard = DOCTOR_TEST_LOCK.lock().expect("locks doctor tests");
        let doctor = DoctorService {
            budget: ProbeBudget::default(),
        };
        let bare_fail = DiagnosticCheck {
            check_id: "fail".to_owned(),
            name: "Fail".to_owned(),
            status: CheckStatus::Fail,
            severity: Severity::Warn,
            duration_seconds: 0.0,
            observed_source: "test".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "test".to_owned(),
                value: None,
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(false),
                notes: Vec::new(),
            },
            issues: Vec::new(),
            feature_readiness: Vec::new(),
        };
        let report = doctor
            .run(vec![Box::new(StaticProbe(bare_fail))])
            .expect("runs");
        assert_eq!(report.aggregate_health, AggregateHealth::Failed);

        let core_blocked = DiagnosticCheck {
            check_id: "core".to_owned(),
            name: "Core".to_owned(),
            status: CheckStatus::Pass,
            severity: Severity::Info,
            duration_seconds: 0.0,
            observed_source: "test".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "test".to_owned(),
                value: None,
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(true),
                notes: Vec::new(),
            },
            issues: Vec::new(),
            feature_readiness: vec![FeatureReadiness {
                feature: "core".to_owned(),
                ready: false,
                blocks_core_start: true,
                blocks_feature_only: false,
                missing_reason: "core prerequisite missing".to_owned(),
            }],
        };
        let report = doctor
            .run(vec![Box::new(StaticProbe(core_blocked))])
            .expect("runs");
        assert_eq!(report.aggregate_health, AggregateHealth::Failed);
    }

    #[test]
    fn bound_check_bounds_hostile_json_and_strings() {
        let budget = ProbeBudget {
            max_output_chars: 512,
            ..ProbeBudget::default()
        };
        let mut check = DiagnosticCheck {
            check_id: "id".repeat(100),
            name: "name".repeat(100),
            status: CheckStatus::Pass,
            severity: Severity::Info,
            duration_seconds: 0.0,
            observed_source: "source".repeat(100),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "source".repeat(100),
                value: Some(serde_json::json!({
                    "huge": "😀".repeat(20_000),
                    "nested": {"a": "x".repeat(10_000)}
                })),
                basename: Some("b".repeat(10_000)),
                digest_prefix: Some("d".repeat(10_000)),
                boolean_presence: Some(true),
                notes: vec!["n".repeat(10_000)],
            },
            issues: vec![DiagnosticIssue {
                code: "c".repeat(10_000),
                severity: Severity::Warn,
                message: "m".repeat(10_000),
                recovery_actions: Vec::new(),
            }],
            feature_readiness: vec![FeatureReadiness {
                feature: "f".repeat(10_000),
                ready: false,
                blocks_core_start: false,
                blocks_feature_only: true,
                missing_reason: "r".repeat(10_000),
            }],
        };
        check = bound_check(check, &budget);
        assert!(check.duration_seconds.is_finite() && check.duration_seconds >= 0.0);
        assert!(check.check_id.len() <= 512);
        assert!(check.name.len() <= 512);
        assert!(check.observed_source.len() <= 512);
        if let Some(value) = &check.evidence.value {
            assert!(
                serde_json::to_vec(value).expect("serializes").len() <= 512,
                "hostile JSON must be bounded"
            );
        }
        assert!(check.evidence.notes.iter().all(|note| note.len() <= 512));
        assert!(check.issues.iter().all(|issue| issue.message.len() <= 512));
    }

    #[test]
    fn bound_json_value_handles_escaping_and_deep_nesting() {
        let limit = 1024;
        let quote_heavy = serde_json::json!({
            "value": "\"\\\u{0001}\u{001f}".repeat(2000)
        });
        assert!(bound_json_value(quote_heavy, limit).is_none());

        let deep = serde_json::json!({ "nested": { "a": [true, [true, [true]]] } });
        let mut deep = deep;
        for _ in 0..200 {
            deep = serde_json::json!([deep]);
        }
        assert!(bound_json_value(deep, 64).is_none());
    }

    #[test]
    fn optional_feature_probe_truncation_never_reports_all_ready() {
        let probe = OptionalFeatureProbe {
            features: (0..100)
                .map(|index| FeatureReadiness {
                    feature: format!("feature-{index}"),
                    ready: index < 20,
                    blocks_core_start: false,
                    blocks_feature_only: true,
                    missing_reason: String::new(),
                })
                .collect(),
        };
        let check = probe.run(&ProbeBudget::default());
        assert_eq!(check.status, CheckStatus::Unavailable);
        assert_eq!(check.evidence.boolean_presence, Some(false));
        assert!(check
            .issues
            .iter()
            .any(|issue| issue.code == "FEATURE_LIST_TRUNCATED"));
        assert!(
            check
                .feature_readiness
                .iter()
                .all(|readiness| readiness.ready),
            "all capped entries are ready; only omitted entries are not"
        );
    }

    #[test]
    fn bound_check_cumulative_budget_includes_evidence_json() {
        let budget = ProbeBudget {
            max_output_chars: 64,
            max_error_count: 4,
            ..ProbeBudget::default()
        };
        let mut check = DiagnosticCheck {
            check_id: "id".to_owned(),
            name: "name".to_owned(),
            status: CheckStatus::Pass,
            severity: Severity::Info,
            duration_seconds: 0.0,
            observed_source: "source".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "source".to_owned(),
                value: Some(serde_json::json!({ "huge": "x".repeat(500) })),
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(true),
                notes: Vec::new(),
            },
            issues: vec![DiagnosticIssue {
                code: "code".to_owned(),
                severity: Severity::Warn,
                message: "y".repeat(500),
                recovery_actions: Vec::new(),
            }],
            feature_readiness: Vec::new(),
        };
        check = bound_check(check, &budget);
        let mut total = check.check_id.len()
            + check.name.len()
            + check.observed_source.len()
            + check.observed_at_iso.len()
            + check.evidence.observed_source.len();
        total += check
            .evidence
            .basename
            .as_deref()
            .map(str::len)
            .unwrap_or(0);
        total += check
            .evidence
            .digest_prefix
            .as_deref()
            .map(str::len)
            .unwrap_or(0);
        total += check.evidence.notes.iter().map(String::len).sum::<usize>();
        if let Some(value) = &check.evidence.value {
            total += serde_json::to_vec(value).expect("serializes").len();
        }
        total += check
            .issues
            .iter()
            .map(|issue| issue.code.len() + issue.message.len())
            .sum::<usize>();
        assert!(
            total <= 512,
            "cumulative dynamic bytes stay bounded, got {total}"
        );
    }

    #[test]
    fn truncate_never_exceeds_remaining_even_below_marker_len() {
        for max in 0..=14 {
            let result = truncate_str(&"x".repeat(100), 100, max);
            assert!(
                result.len() <= max,
                "truncate exceeded {max} with {result:?}"
            );
        }
        let empty = truncate_str("abcdef", 100, 0);
        assert!(empty.is_empty());
        let partial = truncate_str("abcdef", 100, 3);
        assert!(partial.len() <= 3);
    }
}
