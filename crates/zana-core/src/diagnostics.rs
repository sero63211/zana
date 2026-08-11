//! Bounded sequential System Doctor probes and deterministic reports.

use std::path::PathBuf;
use std::time::Instant;

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

pub trait DiagnosticProbe: Send {
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
            status = CheckStatus::Warn;
            severity = Severity::Warn;
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
            status = CheckStatus::Warn;
            severity = Severity::Warn;
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
            feature_readiness: Vec::new(),
        }
    }
}

pub struct SqliteReachabilityProbe {
    pub checker: Box<dyn Fn() -> Result<(String, i64), String> + Send>,
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
    pub state: Box<dyn Fn() -> (Vec<String>, Vec<String>) + Send>,
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

pub struct OptionalDependencyProbe {
    pub packages: Vec<String>,
}

impl Default for OptionalDependencyProbe {
    fn default() -> Self {
        Self {
            packages: ["lancedb", "docling", "zstandard", "mlx_lm", "peft"]
                .into_iter()
                .map(str::to_owned)
                .collect(),
        }
    }
}

impl DiagnosticProbe for OptionalDependencyProbe {
    fn check_id(&self) -> &'static str {
        "optional-dependencies"
    }

    fn name(&self) -> &'static str {
        "Optional dependency metadata"
    }

    fn run(&self, _budget: &ProbeBudget) -> DiagnosticCheck {
        let readiness: Vec<FeatureReadiness> = self
            .packages
            .iter()
            .map(|package| FeatureReadiness {
                feature: package.clone(),
                ready: false,
                blocks_core_start: false,
                blocks_feature_only: true,
                missing_reason: "optional Python package is not bundled in the Rust core"
                    .to_owned(),
            })
            .collect();
        DiagnosticCheck {
            check_id: self.check_id().to_owned(),
            name: self.name().to_owned(),
            status: CheckStatus::Pass,
            severity: Severity::Info,
            duration_seconds: 0.0,
            observed_source: "rust-core-metadata".to_owned(),
            observed_at_iso: now_iso(),
            evidence: Evidence {
                observed_source: "rust-core-metadata".to_owned(),
                value: Some(Value::from(0u64)),
                basename: None,
                digest_prefix: None,
                boolean_presence: Some(true),
                notes: self
                    .packages
                    .iter()
                    .map(|package| format!("{package}:absent"))
                    .collect(),
            },
            issues: Vec::new(),
            feature_readiness: readiness,
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

impl DoctorService {
    pub fn run(&self, probes: Vec<Box<dyn DiagnosticProbe>>) -> Result<DiagnosticReport, String> {
        if probes.len() > self.budget.max_checks {
            return Err("check count exceeds the probe budget".to_owned());
        }
        let started = Instant::now();
        let mut checks = Vec::new();
        for probe in probes {
            if started.elapsed().as_secs_f64() >= self.budget.total_budget_seconds {
                checks.push(self.budget_check("total time budget exceeded"));
                break;
            }
            checks.push(self.run_one(probe.as_ref()));
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
                && check
                    .issues
                    .iter()
                    .any(|issue| issue.severity == Severity::Error)
        });
        let aggregate = if mandatory_failure {
            AggregateHealth::Failed
        } else if checks.iter().any(|check| check.status == CheckStatus::Warn) {
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

    fn run_one(&self, probe: &dyn DiagnosticProbe) -> DiagnosticCheck {
        let started = Instant::now();
        let result =
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| probe.run(&self.budget)));
        let duration = started.elapsed().as_secs_f64();
        match result {
            Ok(check) => check,
            Err(_) => DiagnosticCheck {
                check_id: probe.check_id().to_owned(),
                name: probe.name().to_owned(),
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
                    notes: vec!["probe failed without crashing the report".to_owned()],
                },
                issues: vec![DiagnosticIssue {
                    code: "PROBE_FAILED".to_owned(),
                    severity: Severity::Warn,
                    message: "A diagnostic probe failed; the report remains usable.".to_owned(),
                    recovery_actions: vec![RecoveryAction {
                        code: "RETRY_DOCTOR".to_owned(),
                        message: "Run the doctor again after correcting the environment."
                            .to_owned(),
                        optional: false,
                    }],
                }],
                feature_readiness: Vec::new(),
            },
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

#[cfg(test)]
mod tests {
    use super::*;

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
}
