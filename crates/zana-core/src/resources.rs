//! Bounded resource admission, leases, snapshots, and usage history.

use std::collections::{HashMap, VecDeque};
use std::ffi::CString;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;

use crate::domain::{OperationCategory, PlatformLabel};
use crate::sha256::sha256_hex;

pub const MAX_SAFE_BYTES: i64 = 1 << 62;
pub const RESOURCE_POLICY_REVISION: i64 = 1;
pub const DEFAULT_STALE_AFTER_SECONDS: f64 = 30.0;
pub const MAX_USAGE_PAGE_LIMIT: usize = 200;
pub const DEFAULT_USAGE_HISTORY_LIMIT: usize = 256;
pub const DEFAULT_USAGE_HISTORY_BYTES: usize = 256 * 1024;
pub const MAX_USAGE_HISTORY_LIMIT: usize = 4096;
pub const MAX_USAGE_HISTORY_BYTES: usize = 4 * 1024 * 1024;

pub const HEAVY_CATEGORIES: [OperationCategory; 6] = [
    OperationCategory::Build,
    OperationCategory::EmbeddingIndex,
    OperationCategory::Inference,
    OperationCategory::Training,
    OperationCategory::Export,
    OperationCategory::Portability,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum AdmissionOutcome {
    Allow,
    Ask,
    Block,
}

impl AdmissionOutcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Allow => "allow",
            Self::Ask => "ask",
            Self::Block => "block",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum DenialReason {
    None,
    InvalidRequest,
    Overflow,
    MemoryInsufficient,
    DiskInsufficient,
    ConcurrencyLimit,
    WorkerLimit,
    ItemLimit,
    ByteLimit,
    FileLimit,
    RecursionLimit,
    UnknownSize,
    UnknownHeadroom,
    CategoryLimit,
    StaleSnapshot,
}

impl DenialReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::InvalidRequest => "invalid_request",
            Self::Overflow => "overflow",
            Self::MemoryInsufficient => "memory_insufficient",
            Self::DiskInsufficient => "disk_insufficient",
            Self::ConcurrencyLimit => "concurrency_limit",
            Self::WorkerLimit => "worker_limit",
            Self::ItemLimit => "item_limit",
            Self::ByteLimit => "byte_limit",
            Self::FileLimit => "file_limit",
            Self::RecursionLimit => "recursion_limit",
            Self::UnknownSize => "unknown_size",
            Self::UnknownHeadroom => "unknown_headroom",
            Self::CategoryLimit => "category_limit",
            Self::StaleSnapshot => "stale_snapshot",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum RecoveryAction {
    None,
    ProvideEstimate,
    WaitForHeadroom,
    RetryAfterRelease,
    ReduceWorkers,
    ReduceBatch,
    FreeDisk,
    IncreasePolicyLimit,
    CheckSnapshot,
    Approve,
}

impl RecoveryAction {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::ProvideEstimate => "provide_estimate",
            Self::WaitForHeadroom => "wait_for_headroom",
            Self::RetryAfterRelease => "retry_after_release",
            Self::ReduceWorkers => "reduce_workers",
            Self::ReduceBatch => "reduce_batch",
            Self::FreeDisk => "free_disk",
            Self::IncreasePolicyLimit => "increase_policy_limit",
            Self::CheckSnapshot => "check_snapshot",
            Self::Approve => "approve",
        }
    }
}

#[derive(Debug, Clone)]
pub struct ResourceSnapshot {
    pub revision: i64,
    pub platform: PlatformLabel,
    pub os_name: String,
    pub arch: String,
    pub logical_cores: Option<u32>,
    pub memory_total_bytes: Option<i64>,
    pub memory_available_bytes: Option<i64>,
    pub disk_path: String,
    pub disk_free_bytes: Option<i64>,
    pub probe_error: Option<String>,
    pub notes: Vec<String>,
}

impl ResourceSnapshot {
    pub fn unavailable(revision: i64, probe_error: &str) -> Self {
        Self {
            revision,
            platform: PlatformLabel::Unknown,
            os_name: String::new(),
            arch: String::new(),
            logical_cores: None,
            memory_total_bytes: None,
            memory_available_bytes: None,
            disk_path: String::new(),
            disk_free_bytes: None,
            probe_error: Some(probe_error.to_owned()),
            notes: vec![probe_error.to_owned()],
        }
    }
}

#[derive(Debug, Clone)]
pub struct CategoryLimit {
    pub category: OperationCategory,
    pub max_concurrency: u32,
    pub max_workers: u32,
    pub max_memory_bytes: Option<i64>,
    pub max_disk_bytes: Option<i64>,
    pub max_items: Option<i64>,
    pub max_bytes: Option<i64>,
    pub max_open_files: Option<u32>,
    pub max_recursion_depth: Option<u32>,
    pub tiny: bool,
    pub allow_unknown_size: bool,
}

impl CategoryLimit {
    fn heavy_default(category: OperationCategory) -> Self {
        Self {
            category,
            max_concurrency: 1,
            max_workers: 1,
            max_memory_bytes: None,
            max_disk_bytes: None,
            max_items: None,
            max_bytes: None,
            max_open_files: None,
            max_recursion_depth: None,
            tiny: false,
            allow_unknown_size: false,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ResourcePolicy {
    pub memory_reserve_bytes: i64,
    pub disk_reserve_bytes: i64,
    pub safety_reserve_fraction: f64,
    pub disk_overhead_fraction: f64,
    pub max_open_files: Option<u32>,
    pub max_recursion_depth: Option<u32>,
    pub auto_heavy_concurrency: bool,
    pub max_heavy_concurrency: u32,
    pub large_host_min_memory_bytes: i64,
    pub large_host_min_cores: u32,
    pub categories: Vec<CategoryLimit>,
}

impl Default for ResourcePolicy {
    fn default() -> Self {
        let mut categories = Vec::new();
        categories.push(CategoryLimit {
            category: OperationCategory::Tiny,
            max_concurrency: 16,
            max_workers: 2,
            max_memory_bytes: None,
            max_disk_bytes: None,
            max_items: Some(1000),
            max_bytes: Some(1 << 20),
            max_open_files: None,
            max_recursion_depth: None,
            tiny: true,
            allow_unknown_size: true,
        });
        categories.push(CategoryLimit {
            category: OperationCategory::Metadata,
            max_concurrency: 8,
            max_workers: 2,
            max_memory_bytes: None,
            max_disk_bytes: None,
            max_items: Some(10_000),
            max_bytes: Some(8 << 20),
            max_open_files: None,
            max_recursion_depth: None,
            tiny: true,
            allow_unknown_size: true,
        });
        categories.push(CategoryLimit {
            category: OperationCategory::ReadOnly,
            max_concurrency: 4,
            max_workers: 2,
            max_memory_bytes: None,
            max_disk_bytes: None,
            max_items: Some(100_000),
            max_bytes: Some(256 << 20),
            max_open_files: None,
            max_recursion_depth: None,
            tiny: true,
            allow_unknown_size: true,
        });
        for category in HEAVY_CATEGORIES {
            categories.push(CategoryLimit::heavy_default(category));
        }
        Self {
            memory_reserve_bytes: 1 << 30,
            disk_reserve_bytes: 1 << 30,
            safety_reserve_fraction: 0.15,
            disk_overhead_fraction: 0.5,
            max_open_files: Some(512),
            max_recursion_depth: Some(64),
            auto_heavy_concurrency: true,
            max_heavy_concurrency: 2,
            large_host_min_memory_bytes: 32 << 30,
            large_host_min_cores: 8,
            categories,
        }
    }
}

impl ResourcePolicy {
    pub fn category_limit(&self, category: OperationCategory) -> CategoryLimit {
        self.categories
            .iter()
            .find(|limit| limit.category == category)
            .cloned()
            .unwrap_or_else(|| CategoryLimit::heavy_default(category))
    }
}

#[derive(Debug, Clone)]
pub struct OperationRequest {
    pub id: String,
    pub category: OperationCategory,
    pub name: String,
    pub required_memory_bytes: Option<i64>,
    pub required_disk_bytes: Option<i64>,
    pub requested_workers: Option<u32>,
    pub items_count: Option<i64>,
    pub byte_count: Option<i64>,
    pub open_files: Option<u32>,
    pub recursion_depth: Option<u32>,
    pub ttl_seconds: Option<i64>,
}

#[derive(Debug, Clone)]
pub struct ResourceLease {
    pub token: String,
    pub request_id: String,
    pub category: OperationCategory,
    pub policy_revision: i64,
    pub snapshot_revision: i64,
    pub memory_bytes: i64,
    pub disk_bytes: i64,
    pub workers: u32,
    pub items: i64,
    pub bytes_accounted: i64,
    pub open_files: u32,
    pub active: bool,
}

#[derive(Debug, Clone)]
pub struct AdmissionDecision {
    pub request_id: String,
    pub category: OperationCategory,
    pub outcome: AdmissionOutcome,
    pub reason: DenialReason,
    pub recovery: RecoveryAction,
    pub detail: String,
    pub snapshot_revision: i64,
    pub lease: Option<ResourceLease>,
}

#[derive(Debug, Clone)]
pub struct UsageRecord {
    pub token: String,
    pub request_id: String,
    pub category: OperationCategory,
    pub policy_revision: i64,
    pub snapshot_revision: i64,
    pub memory_bytes: i64,
    pub disk_bytes: i64,
    pub workers: u32,
    pub items: i64,
    pub bytes_accounted: i64,
    pub open_files: u32,
    pub released: bool,
    pub sequence: i64,
}

pub trait SnapshotProvider: Send {
    fn capture(&self) -> ResourceSnapshot;
}

pub struct DefaultSnapshotProvider {
    workspace: String,
}

impl DefaultSnapshotProvider {
    pub fn new(workspace: impl Into<String>) -> Self {
        Self {
            workspace: workspace.into(),
        }
    }
}

impl SnapshotProvider for DefaultSnapshotProvider {
    fn capture(&self) -> ResourceSnapshot {
        capture_host_snapshot(&self.workspace, 0)
    }
}

pub fn capture_host_snapshot(workspace: &str, revision: i64) -> ResourceSnapshot {
    let (memory_total, memory_available, mut probe_error) = memory_probe();
    let (disk_free, disk_error) = disk_probe(workspace);
    if let Some(error) = disk_error {
        probe_error = Some(match probe_error {
            Some(memory) => format!("{memory}; {error}"),
            None => error,
        });
    }
    let cores = std::thread::available_parallelism()
        .ok()
        .map(|value| value.get() as u32);
    let platform = current_platform_label();
    let os_name = std::env::consts::OS.to_owned();
    let arch = std::env::consts::ARCH.to_owned();
    let mut notes = Vec::new();
    if let Some(error) = &probe_error {
        notes.push(error.clone());
    }
    ResourceSnapshot {
        revision,
        platform,
        os_name,
        arch,
        logical_cores: cores,
        memory_total_bytes: memory_total,
        memory_available_bytes: memory_available,
        disk_path: workspace.to_owned(),
        disk_free_bytes: disk_free,
        probe_error,
        notes,
    }
}

#[cfg(unix)]
fn memory_probe() -> (Option<i64>, Option<i64>, Option<String>) {
    #[cfg(target_os = "macos")]
    {
        macos_memory()
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        linux_memory()
    }
}

#[cfg(not(unix))]
fn memory_probe() -> (Option<i64>, Option<i64>, Option<String>) {
    (
        None,
        None,
        Some("memory probe unavailable on this platform".to_owned()),
    )
}

#[cfg(all(unix, not(target_os = "macos")))]
fn linux_memory() -> (Option<i64>, Option<i64>, Option<String>) {
    let content = match std::fs::read_to_string("/proc/meminfo") {
        Ok(content) => content,
        Err(error) => {
            return (None, None, Some(format!("memory probe failed: {error}")));
        }
    };
    let mut total = None;
    let mut available = None;
    for line in content.lines().take(64) {
        if line.starts_with("MemTotal:") {
            total = parse_kb(line);
        } else if line.starts_with("MemAvailable:") {
            available = parse_kb(line);
        }
    }
    if total.is_none() || available.is_none() {
        return (
            total,
            available,
            Some("memory probe failed: /proc/meminfo missing fields".to_owned()),
        );
    }
    (total, available, None)
}

#[cfg(all(unix, not(target_os = "macos")))]
fn parse_kb(line: &str) -> Option<i64> {
    let value = line.split_whitespace().nth(1)?;
    value.parse::<i64>().ok()?.checked_mul(1024)
}

#[cfg(target_os = "macos")]
#[allow(deprecated)] // libc exposes the required mach port API; no new dependency is permitted.
fn macos_memory() -> (Option<i64>, Option<i64>, Option<String>) {
    let total = unsafe {
        let mut size = 8usize;
        let mut value: u64 = 0;
        let name = c"hw.memsize".as_ptr();
        if libc::sysctlbyname(
            name,
            (&mut value as *mut u64).cast(),
            &mut size,
            std::ptr::null_mut(),
            0,
        ) != 0
        {
            None
        } else {
            i64::try_from(value).ok()
        }
    };
    let available = unsafe {
        let mut count = libc::HOST_VM_INFO64_COUNT;
        let mut info: libc::vm_statistics64 = std::mem::zeroed();
        let host = libc::mach_host_self();
        let result = libc::host_statistics64(
            host,
            libc::HOST_VM_INFO64,
            (&mut info as *mut libc::vm_statistics64).cast(),
            &mut count,
        );
        if result != libc::KERN_SUCCESS {
            None
        } else {
            let page_size = libc::vm_page_size as i64;
            let free = i64::from(info.free_count);
            let inactive = i64::from(info.inactive_count);
            free.checked_add(inactive * page_size)
        }
    };
    if total.is_none() || available.is_none() {
        return (
            total,
            available,
            Some("memory probe failed: macOS sysctl or host statistics unavailable".to_owned()),
        );
    }
    (total, available, None)
}

#[cfg(unix)]
fn disk_probe(path: &str) -> (Option<i64>, Option<String>) {
    let c_path = match CString::new(path.as_bytes()) {
        Ok(value) => value,
        Err(_) => return (None, Some("disk probe failed: invalid path".to_owned())),
    };
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    let result = unsafe { libc::statvfs(c_path.as_ptr(), &mut stat) };
    if result != 0 {
        return (
            None,
            Some(format!("disk probe failed: statvfs error {result}")),
        );
    }
    let free = (stat.f_bavail as u64)
        .checked_mul(stat.f_frsize as u64)
        .and_then(|value| i64::try_from(value).ok());
    (free, None)
}

#[cfg(not(unix))]
fn disk_probe(_path: &str) -> (Option<i64>, Option<String>) {
    (
        None,
        Some("disk probe unavailable on this platform".to_owned()),
    )
}

#[cfg(target_os = "macos")]
fn current_platform_label() -> PlatformLabel {
    PlatformLabel::Macos
}

#[cfg(all(unix, not(target_os = "macos")))]
fn current_platform_label() -> PlatformLabel {
    PlatformLabel::Linux
}

#[cfg(windows)]
fn current_platform_label() -> PlatformLabel {
    PlatformLabel::Windows
}

#[cfg(not(any(target_os = "macos", all(unix, not(target_os = "macos")), windows)))]
fn current_platform_label() -> PlatformLabel {
    PlatformLabel::Unknown
}

pub type Now = dyn Fn() -> i64 + Send;

pub struct ResourceGovernor {
    policy: ResourcePolicy,
    provider: Box<dyn SnapshotProvider>,
    now: Box<Now>,
    token_counter: i64,
    record_counter: i64,
    leases: HashMap<String, ResourceLease>,
    records: VecDeque<UsageRecord>,
    records_bytes: i64,
    history_dropped_count: i64,
    history_dropped_bytes: i64,
    usage_history_limit: usize,
    usage_history_max_bytes: usize,
    expiry: HashMap<String, i64>,
    active_memory: i64,
    active_disk: i64,
    active_items: i64,
    active_bytes: i64,
    active_files: i64,
    active_workers: i64,
    category_counts: HashMap<OperationCategory, i64>,
    category_items: HashMap<OperationCategory, i64>,
    category_bytes: HashMap<OperationCategory, i64>,
    category_files: HashMap<OperationCategory, i64>,
    snapshot_revision: i64,
    snapshot: ResourceSnapshot,
}

impl ResourceGovernor {
    pub fn new(policy: ResourcePolicy, provider: Box<dyn SnapshotProvider>, now: Box<Now>) -> Self {
        let mut governor = Self {
            policy,
            provider,
            now,
            token_counter: 0,
            record_counter: 0,
            leases: HashMap::new(),
            records: VecDeque::new(),
            records_bytes: 0,
            history_dropped_count: 0,
            history_dropped_bytes: 0,
            usage_history_limit: DEFAULT_USAGE_HISTORY_LIMIT,
            usage_history_max_bytes: DEFAULT_USAGE_HISTORY_BYTES,
            expiry: HashMap::new(),
            active_memory: 0,
            active_disk: 0,
            active_items: 0,
            active_bytes: 0,
            active_files: 0,
            active_workers: 0,
            category_counts: HashMap::new(),
            category_items: HashMap::new(),
            category_bytes: HashMap::new(),
            category_files: HashMap::new(),
            snapshot_revision: 0,
            snapshot: ResourceSnapshot::unavailable(0, "no snapshot captured yet"),
        };
        let captured = governor.provider.capture();
        governor.snapshot = ResourceSnapshot {
            revision: 0,
            ..captured
        };
        governor
    }

    pub fn policy(&self) -> &ResourcePolicy {
        &self.policy
    }

    pub fn snapshot(&self) -> &ResourceSnapshot {
        &self.snapshot
    }

    pub fn refresh(&mut self) {
        self.reap_expired();
        self.snapshot_revision += 1;
        let mut captured = self.provider.capture();
        captured.revision = self.snapshot_revision;
        self.snapshot = captured;
    }

    pub fn configure_usage_history(&mut self, limit: usize, max_bytes: usize) {
        self.usage_history_limit = limit;
        self.usage_history_max_bytes = max_bytes;
        self.trim_usage_history();
    }

    pub fn admit(&mut self, request: &OperationRequest) -> AdmissionDecision {
        self.reap_expired();
        let limit = self.policy.category_limit(request.category);
        if let Some(open_files) = request.open_files {
            if open_files > 0
                && self
                    .policy
                    .max_open_files
                    .is_some_and(|cap| open_files > cap)
            {
                return self.block(
                    request,
                    DenialReason::FileLimit,
                    RecoveryAction::ReduceBatch,
                    format!(
                        "requested open_files {open_files} exceeds policy cap {}",
                        self.policy.max_open_files.unwrap_or(0)
                    ),
                );
            }
        }
        if let Some(depth) = request.recursion_depth {
            if self
                .policy
                .max_recursion_depth
                .is_some_and(|cap| depth > cap)
            {
                return self.block(
                    request,
                    DenialReason::RecursionLimit,
                    RecoveryAction::ReduceBatch,
                    format!(
                        "requested recursion_depth {depth} exceeds policy cap {}",
                        self.policy.max_recursion_depth.unwrap_or(0)
                    ),
                );
            }
        }

        let workers = request.requested_workers.unwrap_or(limit.max_workers);
        if request
            .requested_workers
            .is_some_and(|value| value > limit.max_workers)
        {
            return self.block(
                request,
                DenialReason::WorkerLimit,
                RecoveryAction::ReduceWorkers,
                format!(
                    "requested workers {} exceeds category cap {}",
                    request.requested_workers.unwrap_or(0),
                    limit.max_workers
                ),
            );
        }

        let effective = self.effective_concurrency(&limit);
        let active_count = self
            .category_counts
            .get(&request.category)
            .copied()
            .unwrap_or(0);
        if active_count >= i64::from(effective) {
            return self.block(
                request,
                DenialReason::ConcurrencyLimit,
                RecoveryAction::RetryAfterRelease,
                format!(
                    "category {} already has {active_count} active operations; cap is {effective}",
                    request.category.as_str()
                ),
            );
        }

        if let Some(items) = request.items_count {
            let active_items = self
                .category_items
                .get(&request.category)
                .copied()
                .unwrap_or(0);
            if limit
                .max_items
                .is_some_and(|cap| items + active_items > cap)
            {
                return self.block(
                    request,
                    DenialReason::ItemLimit,
                    RecoveryAction::ReduceBatch,
                    format!(
                        "items {items} plus active {active_items} exceed cap {}",
                        limit.max_items.unwrap_or(0)
                    ),
                );
            }
        }
        if let Some(bytes) = request.byte_count {
            let active_bytes = self
                .category_bytes
                .get(&request.category)
                .copied()
                .unwrap_or(0);
            if limit
                .max_bytes
                .is_some_and(|cap| bytes + active_bytes > cap)
            {
                return self.block(
                    request,
                    DenialReason::ByteLimit,
                    RecoveryAction::ReduceBatch,
                    format!(
                        "bytes {bytes} plus active {active_bytes} exceed cap {}",
                        limit.max_bytes.unwrap_or(0)
                    ),
                );
            }
        }

        if let Some(decision) = self.check_memory(request, &limit) {
            return decision;
        }
        if let Some(decision) = self.check_disk(request, &limit) {
            return decision;
        }

        let memory_bytes = request.required_memory_bytes.unwrap_or(0);
        let disk_bytes = self.disk_requirement(request);
        let items = request.items_count.unwrap_or(0);
        let byte_count = request.byte_count.unwrap_or(0);
        let open_files = request.open_files.unwrap_or(0);

        self.token_counter += 1;
        let token = format!("L-{:06}", self.token_counter);
        let lease = ResourceLease {
            token: token.clone(),
            request_id: request.id.clone(),
            category: request.category,
            policy_revision: RESOURCE_POLICY_REVISION,
            snapshot_revision: self.snapshot_revision,
            memory_bytes,
            disk_bytes,
            workers,
            items,
            bytes_accounted: byte_count,
            open_files,
            active: true,
        };
        self.leases.insert(token.clone(), lease.clone());
        if let Some(ttl) = request.ttl_seconds {
            self.expiry.insert(token.clone(), (self.now)() + ttl * 1000);
        }
        self.active_memory += memory_bytes;
        self.active_disk += disk_bytes;
        self.active_items += items;
        self.active_bytes += byte_count;
        self.active_files += i64::from(open_files);
        self.active_workers += i64::from(workers);
        *self.category_counts.entry(request.category).or_insert(0) += 1;
        *self.category_items.entry(request.category).or_insert(0) += items;
        *self.category_bytes.entry(request.category).or_insert(0) += byte_count;
        *self.category_files.entry(request.category).or_insert(0) += i64::from(open_files);
        self.append_record(&lease, false);
        AdmissionDecision {
            request_id: request.id.clone(),
            category: request.category,
            outcome: AdmissionOutcome::Allow,
            reason: DenialReason::None,
            recovery: RecoveryAction::None,
            detail: "admitted".to_owned(),
            snapshot_revision: self.snapshot_revision,
            lease: Some(lease),
        }
    }

    fn check_memory(
        &self,
        request: &OperationRequest,
        limit: &CategoryLimit,
    ) -> Option<AdmissionDecision> {
        let Some(required) = request.required_memory_bytes else {
            if limit.tiny || limit.allow_unknown_size {
                return None;
            }
            return Some(AdmissionDecision {
                request_id: request.id.clone(),
                category: request.category,
                outcome: AdmissionOutcome::Ask,
                reason: DenialReason::UnknownSize,
                recovery: RecoveryAction::ProvideEstimate,
                detail: "required memory is unknown; provide an explicit estimate or approval"
                    .to_owned(),
                snapshot_revision: self.snapshot_revision,
                lease: None,
            });
        };
        if limit.max_memory_bytes.is_some_and(|cap| required > cap) {
            return Some(self.block(
                request,
                DenialReason::CategoryLimit,
                RecoveryAction::IncreasePolicyLimit,
                format!(
                    "required memory {required} exceeds category cap {}",
                    limit.max_memory_bytes.unwrap_or(0)
                ),
            ));
        }
        let budget = self.memory_budget();
        if budget.is_none() {
            if limit.tiny || limit.allow_unknown_size {
                return None;
            }
            return Some(AdmissionDecision {
                request_id: request.id.clone(),
                category: request.category,
                outcome: AdmissionOutcome::Ask,
                reason: DenialReason::UnknownHeadroom,
                recovery: RecoveryAction::CheckSnapshot,
                detail: "memory headroom is unknown; cannot prove safety".to_owned(),
                snapshot_revision: self.snapshot_revision,
                lease: None,
            });
        }
        if required + self.active_memory > budget.unwrap_or(0) {
            return Some(self.block(
                request,
                DenialReason::MemoryInsufficient,
                RecoveryAction::WaitForHeadroom,
                format!(
                    "required memory {required} plus active {} exceeds budget {}",
                    self.active_memory,
                    budget.unwrap_or(0)
                ),
            ));
        }
        None
    }

    fn check_disk(
        &self,
        request: &OperationRequest,
        limit: &CategoryLimit,
    ) -> Option<AdmissionDecision> {
        let Some(_required) = request.required_disk_bytes else {
            if limit.tiny || limit.allow_unknown_size {
                return None;
            }
            return Some(AdmissionDecision {
                request_id: request.id.clone(),
                category: request.category,
                outcome: AdmissionOutcome::Ask,
                reason: DenialReason::UnknownSize,
                recovery: RecoveryAction::ProvideEstimate,
                detail: "required disk is unknown; provide an explicit estimate or approval"
                    .to_owned(),
                snapshot_revision: self.snapshot_revision,
                lease: None,
            });
        };
        let requirement = self.disk_requirement(request);
        if limit.max_disk_bytes.is_some_and(|cap| requirement > cap) {
            return Some(self.block(
                request,
                DenialReason::CategoryLimit,
                RecoveryAction::IncreasePolicyLimit,
                format!(
                    "disk requirement {requirement} exceeds category cap {}",
                    limit.max_disk_bytes.unwrap_or(0)
                ),
            ));
        }
        let budget = self.disk_budget();
        if budget.is_none() {
            if limit.tiny || limit.allow_unknown_size {
                return None;
            }
            return Some(AdmissionDecision {
                request_id: request.id.clone(),
                category: request.category,
                outcome: AdmissionOutcome::Ask,
                reason: DenialReason::UnknownHeadroom,
                recovery: RecoveryAction::CheckSnapshot,
                detail: "disk headroom is unknown; cannot prove safety".to_owned(),
                snapshot_revision: self.snapshot_revision,
                lease: None,
            });
        }
        if requirement + self.active_disk > budget.unwrap_or(0) {
            return Some(self.block(
                request,
                DenialReason::DiskInsufficient,
                RecoveryAction::FreeDisk,
                format!(
                    "disk requirement {requirement} plus active {} exceeds budget {}",
                    self.active_disk,
                    budget.unwrap_or(0)
                ),
            ));
        }
        None
    }

    fn disk_requirement(&self, request: &OperationRequest) -> i64 {
        let Some(required) = request.required_disk_bytes else {
            return 0;
        };
        (required as f64 * (1.0 + self.policy.disk_overhead_fraction)) as i64
    }

    fn memory_budget(&self) -> Option<i64> {
        let available = self.snapshot.memory_available_bytes?;
        let total = self.snapshot.memory_total_bytes?;
        let safety = (total as f64 * self.policy.safety_reserve_fraction) as i64;
        Some((available - self.policy.memory_reserve_bytes - safety).max(0))
    }

    fn disk_budget(&self) -> Option<i64> {
        Some((self.snapshot.disk_free_bytes? - self.policy.disk_reserve_bytes).max(0))
    }

    fn effective_concurrency(&self, limit: &CategoryLimit) -> u32 {
        if !self.policy.auto_heavy_concurrency || !HEAVY_CATEGORIES.contains(&limit.category) {
            return limit.max_concurrency;
        }
        let large = self
            .snapshot
            .memory_total_bytes
            .is_some_and(|memory| memory >= self.policy.large_host_min_memory_bytes)
            && self
                .snapshot
                .logical_cores
                .is_some_and(|cores| cores >= self.policy.large_host_min_cores);
        let host_cap = if large {
            self.policy.max_heavy_concurrency
        } else {
            1
        };
        limit.max_concurrency.min(host_cap)
    }

    fn block(
        &self,
        request: &OperationRequest,
        reason: DenialReason,
        recovery: RecoveryAction,
        detail: String,
    ) -> AdmissionDecision {
        AdmissionDecision {
            request_id: request.id.clone(),
            category: request.category,
            outcome: AdmissionOutcome::Block,
            reason,
            recovery,
            detail,
            snapshot_revision: self.snapshot_revision,
            lease: None,
        }
    }

    pub fn release(&mut self, token: &str) -> Result<UsageRecord, String> {
        self.reap_expired();
        let lease = self
            .leases
            .remove(token)
            .ok_or_else(|| "unknown lease".to_owned())?;
        self.active_memory = (self.active_memory - lease.memory_bytes).max(0);
        self.active_disk = (self.active_disk - lease.disk_bytes).max(0);
        self.active_items = (self.active_items - lease.items).max(0);
        self.active_bytes = (self.active_bytes - lease.bytes_accounted).max(0);
        self.active_files = (self.active_files - i64::from(lease.open_files)).max(0);
        self.active_workers = (self.active_workers - i64::from(lease.workers)).max(0);
        *self.category_counts.entry(lease.category).or_insert(0) -= 1;
        *self.category_items.entry(lease.category).or_insert(0) -= lease.items;
        *self.category_bytes.entry(lease.category).or_insert(0) -= lease.bytes_accounted;
        *self.category_files.entry(lease.category).or_insert(0) -= i64::from(lease.open_files);
        self.expiry.remove(token);
        let mut released = lease;
        released.active = false;
        let record = self.append_record(&released, true);
        Ok(record)
    }

    pub fn reap_expired(&mut self) {
        let now = (self.now)();
        let expired: Vec<String> = self
            .expiry
            .iter()
            .filter(|(_, deadline)| **deadline <= now)
            .map(|(token, _)| token.clone())
            .collect();
        for token in expired {
            let _ = self.release(&token);
        }
    }

    pub fn active_leases(&self) -> Vec<ResourceLease> {
        self.leases.values().cloned().collect()
    }

    pub fn usage_records(&self) -> Vec<UsageRecord> {
        self.records.iter().cloned().collect()
    }

    pub fn usage_history_stats(&self) -> (i64, i64, i64, i64) {
        (
            self.records.len() as i64,
            self.records_bytes,
            self.history_dropped_count,
            self.history_dropped_bytes,
        )
    }

    fn append_record(&mut self, lease: &ResourceLease, released: bool) -> UsageRecord {
        self.record_counter += 1;
        let record = UsageRecord {
            token: lease.token.clone(),
            request_id: lease.request_id.clone(),
            category: lease.category,
            policy_revision: lease.policy_revision,
            snapshot_revision: lease.snapshot_revision,
            memory_bytes: lease.memory_bytes,
            disk_bytes: lease.disk_bytes,
            workers: lease.workers,
            items: lease.items,
            bytes_accounted: lease.bytes_accounted,
            open_files: lease.open_files,
            released,
            sequence: self.record_counter,
        };
        let size = record_size(&record);
        self.records.push_back(record.clone());
        self.records_bytes += size;
        self.trim_usage_history();
        record
    }

    fn trim_usage_history(&mut self) {
        loop {
            let over_limit = self.records.len() > self.usage_history_limit
                || self.records_bytes > self.usage_history_max_bytes as i64;
            let Some(removed) = self.records.pop_front() else {
                break;
            };
            if !over_limit {
                self.records.push_front(removed);
                break;
            }
            let size = record_size(&removed);
            self.records_bytes -= size;
            self.history_dropped_count += 1;
            self.history_dropped_bytes += size;
        }
    }
}

fn record_size(record: &UsageRecord) -> i64 {
    let mut size = record.token.len() + record.request_id.len();
    size += 96;
    i64::try_from(size).unwrap_or(i64::MAX)
}

pub struct SnapshotView {
    pub revision: i64,
    pub captured_at_iso: String,
    pub age_seconds: f64,
    pub fresh: bool,
    pub platform: PlatformLabel,
    pub os_name: String,
    pub arch: String,
    pub logical_cores: Option<u32>,
    pub memory_total_bytes: Option<i64>,
    pub memory_available_bytes: Option<i64>,
    pub disk_path: String,
    pub disk_free_bytes: Option<i64>,
    pub probe_error_code: Option<String>,
    pub probe_status: String,
}

pub struct UsagePageView {
    pub items: Vec<UsageRecord>,
    pub count: usize,
    pub limit: usize,
    pub next_cursor: Option<i64>,
    pub truncated: bool,
    pub total_available: usize,
    pub history_limit: usize,
    pub history_dropped: i64,
    pub history_max_bytes: usize,
    pub history_serialized_bytes: i64,
    pub history_serialized_bytes_dropped: i64,
}

pub struct ResourceService {
    governor: Mutex<ResourceGovernor>,
    captured_at: Mutex<i64>,
    captured_at_iso: Mutex<String>,
    stale_after_millis: i64,
    usage_history_limit: usize,
    usage_history_max_bytes: usize,
}

impl ResourceService {
    pub fn new(governor: ResourceGovernor, stale_after_seconds: f64) -> Self {
        let millis = (stale_after_seconds.max(0.0) * 1000.0) as i64;
        let now = unix_millis();
        Self {
            governor: Mutex::new(governor),
            captured_at: Mutex::new(now),
            captured_at_iso: Mutex::new(crate::time::now_iso()),
            stale_after_millis: millis,
            usage_history_limit: DEFAULT_USAGE_HISTORY_LIMIT,
            usage_history_max_bytes: DEFAULT_USAGE_HISTORY_BYTES,
        }
    }

    pub fn snapshot(&self) -> SnapshotView {
        let mut governor = lock(&self.governor);
        governor.reap_expired();
        self.view(&governor)
    }

    pub fn refresh(&self) -> SnapshotView {
        let mut governor = lock(&self.governor);
        governor.refresh();
        *lock(&self.captured_at) = unix_millis();
        *lock(&self.captured_at_iso) = crate::time::now_iso();
        self.view(&governor)
    }

    pub fn policy(&self) -> ResourcePolicy {
        lock(&self.governor).policy().clone()
    }

    pub fn active_leases(&self) -> Vec<ResourceLease> {
        let mut governor = lock(&self.governor);
        governor.reap_expired();
        governor.active_leases()
    }

    pub fn admit(&self, request: &OperationRequest) -> AdmissionDecision {
        lock(&self.governor).admit(request)
    }

    pub fn release(&self, token: &str) -> Result<UsageRecord, String> {
        lock(&self.governor).release(token)
    }

    pub fn usage_page(
        &self,
        limit: usize,
        before_sequence: Option<i64>,
    ) -> Result<UsagePageView, String> {
        if !(1..=MAX_USAGE_PAGE_LIMIT).contains(&limit) {
            return Err("limit must be within the usage page cap".to_owned());
        }
        if before_sequence.is_some_and(|value| value < 0) {
            return Err("before_sequence must be non-negative".to_owned());
        }
        let mut governor = lock(&self.governor);
        governor.reap_expired();
        let mut records = governor.usage_records();
        if let Some(before) = before_sequence {
            records.retain(|record| record.sequence < before);
        }
        let total = records.len();
        let newest: Vec<UsageRecord> = records.iter().rev().take(limit).cloned().collect();
        let truncated = total > newest.len();
        let next_cursor = if truncated && !newest.is_empty() {
            Some(newest.last().expect("non-empty").sequence)
        } else {
            None
        };
        let (_, retained_bytes, dropped, dropped_bytes) = governor.usage_history_stats();
        let count = newest.len();
        Ok(UsagePageView {
            items: newest,
            count,
            limit,
            next_cursor,
            truncated,
            total_available: total,
            history_limit: self.usage_history_limit,
            history_dropped: dropped,
            history_max_bytes: self.usage_history_max_bytes,
            history_serialized_bytes: retained_bytes,
            history_serialized_bytes_dropped: dropped_bytes,
        })
    }

    fn view(&self, governor: &ResourceGovernor) -> SnapshotView {
        let snapshot = governor.snapshot();
        let now = unix_millis();
        let captured = *lock(&self.captured_at);
        let age = ((now - captured).max(0) as f64) / 1000.0;
        SnapshotView {
            revision: snapshot.revision,
            captured_at_iso: lock(&self.captured_at_iso).clone(),
            age_seconds: (age * 1000.0).round() / 1000.0,
            fresh: age <= self.stale_after_millis as f64,
            platform: snapshot.platform,
            os_name: snapshot.os_name.clone(),
            arch: snapshot.arch.clone(),
            logical_cores: snapshot.logical_cores,
            memory_total_bytes: snapshot.memory_total_bytes,
            memory_available_bytes: snapshot.memory_available_bytes,
            disk_path: redact_path(&snapshot.disk_path),
            disk_free_bytes: snapshot.disk_free_bytes,
            probe_error_code: probe_error_code(snapshot.probe_error.as_deref()),
            probe_status: probe_status(snapshot).to_owned(),
        }
    }
}

fn probe_status(snapshot: &ResourceSnapshot) -> &'static str {
    let values = [
        snapshot.memory_total_bytes,
        snapshot.memory_available_bytes,
        snapshot.disk_free_bytes,
    ];
    if values.iter().all(|value| value.is_some()) {
        "ok"
    } else if values.iter().any(|value| value.is_some()) {
        "partial"
    } else {
        "unavailable"
    }
}

fn probe_error_code(error: Option<&str>) -> Option<String> {
    let error = error?;
    if error.contains("memory probe failed") {
        Some("MEMORY_PROBE_UNAVAILABLE".to_owned())
    } else if error.contains("disk probe failed") {
        Some("DISK_PROBE_UNAVAILABLE".to_owned())
    } else {
        Some("PROBE_UNAVAILABLE".to_owned())
    }
}

pub fn redact_path(value: &str) -> String {
    if value.is_empty() {
        return String::new();
    }
    if value.len() > 4096 {
        return format!(
            "path-{}-len{}",
            &sha256_hex(&value.as_bytes()[..4096])[..16],
            value.len()
        );
    }
    let normalized = value.replace('\\', "/");
    let basename = normalized.rsplit('/').next().unwrap_or(value);
    if basename.is_empty() || basename == "." || basename == ".." || basename.len() > 128 {
        format!(
            "path-{}-len{}",
            &sha256_hex(value.as_bytes())[..16],
            value.len()
        )
    } else {
        basename.to_owned()
    }
}

pub fn public_lease_ref(token: &str) -> String {
    let digest = sha256_hex(format!("zana-resource-lease-ref-v1{token}").as_bytes());
    format!("lease-{}", &digest[..16])
}

pub fn public_request_ref(request_id: &str) -> String {
    if request_id.is_empty() {
        return String::new();
    }
    let digest = sha256_hex(format!("zana-resource-request-ref-v1{request_id}").as_bytes());
    format!("request-{}", &digest[..16])
}

fn unix_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(i64::MAX)
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    match mutex.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FakeSnapshot;

    impl SnapshotProvider for FakeSnapshot {
        fn capture(&self) -> ResourceSnapshot {
            ResourceSnapshot {
                revision: 0,
                platform: PlatformLabel::Linux,
                os_name: "linux".to_owned(),
                arch: "x86_64".to_owned(),
                logical_cores: Some(16),
                memory_total_bytes: Some(64 << 30),
                memory_available_bytes: Some(48 << 30),
                disk_path: "/private/var/root".to_owned(),
                disk_free_bytes: Some(100 << 30),
                probe_error: None,
                notes: Vec::new(),
            }
        }
    }

    fn governor() -> ResourceGovernor {
        ResourceGovernor::new(
            ResourcePolicy::default(),
            Box::new(FakeSnapshot),
            Box::new(|| 1_000_000_000_000),
        )
    }

    fn request(category: OperationCategory) -> OperationRequest {
        OperationRequest {
            id: "req-1".to_owned(),
            category,
            name: "test".to_owned(),
            required_memory_bytes: Some(1 << 20),
            required_disk_bytes: Some(1 << 20),
            requested_workers: Some(1),
            items_count: Some(1),
            byte_count: Some(16),
            open_files: Some(1),
            recursion_depth: Some(1),
            ttl_seconds: None,
        }
    }

    #[test]
    fn admits_releases_and_rejects_unknown_headroom() {
        let mut governor = governor();
        let decision = governor.admit(&request(OperationCategory::Inference));
        assert_eq!(decision.outcome, AdmissionOutcome::Allow);
        let token = decision.lease.expect("lease").token;
        assert!(governor.release(&token).is_ok());
        assert!(governor.release(&token).is_err(), "double release fails");

        let unknown = OperationRequest {
            required_memory_bytes: None,
            required_disk_bytes: None,
            ..request(OperationCategory::Inference)
        };
        let decision = governor.admit(&unknown);
        assert_eq!(decision.outcome, AdmissionOutcome::Ask);
        assert_eq!(decision.reason, DenialReason::UnknownSize);
    }

    #[test]
    fn concurrency_and_disk_budgets_are_enforced() {
        let mut governor = governor();
        let first = governor.admit(&request(OperationCategory::Build));
        assert_eq!(first.outcome, AdmissionOutcome::Allow);
        let second = governor.admit(&request(OperationCategory::Build));
        assert_eq!(second.outcome, AdmissionOutcome::Block);
        assert_eq!(second.reason, DenialReason::ConcurrencyLimit);
        governor
            .release(&first.lease.expect("lease").token)
            .expect("releases");
        let third = governor.admit(&request(OperationCategory::Build));
        assert_eq!(third.outcome, AdmissionOutcome::Allow);

        let huge_disk = OperationRequest {
            required_disk_bytes: Some(200 << 30),
            ..request(OperationCategory::Inference)
        };
        let blocked = governor.admit(&huge_disk);
        assert_eq!(blocked.reason, DenialReason::DiskInsufficient);
    }

    #[test]
    fn service_pages_usage_newest_first() {
        let service = ResourceService::new(governor(), 30.0);
        for index in 0..3 {
            let decision = service.admit(&OperationRequest {
                id: format!("req-{index}"),
                ..request(OperationCategory::Tiny)
            });
            service
                .release(&decision.lease.expect("lease").token)
                .expect("releases");
        }
        let page = service.usage_page(2, None).expect("pages");
        assert_eq!(page.items.len(), 2);
        assert_eq!(page.items[0].sequence, 6);
        assert_eq!(page.items[1].sequence, 5);
        assert_eq!(page.next_cursor, Some(5));
        let older = service.usage_page(10, page.next_cursor).expect("pages");
        assert_eq!(older.items.len(), 4);
    }

    #[test]
    fn path_redaction_keeps_basename_or_digest() {
        assert_eq!(
            redact_path("/Users/zana/Library/Application Support/zana"),
            "zana"
        );
        let long = "x".repeat(5000);
        let redacted = redact_path(&format!("/a/{long}"));
        assert!(redacted.starts_with("path-"));
        assert!(redacted.contains("-len"));
    }

    #[test]
    fn usage_history_is_bounded() {
        let mut governor = governor();
        governor.configure_usage_history(4, 1 << 10);
        for index in 0..8 {
            let decision = governor.admit(&OperationRequest {
                id: format!("req-{index}"),
                ..request(OperationCategory::Tiny)
            });
            governor
                .release(&decision.lease.expect("lease").token)
                .expect("releases");
        }
        let (retained, _, dropped, _) = governor.usage_history_stats();
        assert_eq!(retained, 4);
        assert_eq!(dropped, 12);
    }
}
