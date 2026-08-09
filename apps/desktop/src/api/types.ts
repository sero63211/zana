export type RuntimeKind =
  | "ollama"
  | "lm-studio"
  | "llama.cpp"
  | "mlx-lm"
  | "openai-compatible"
  | "unknown";

export type RuntimeSource = "auto" | "manual";

export type RuntimeStatus = "unknown" | "online" | "offline" | "error";

export type ModelIdentityStrength =
  | "unknown"
  | "exact_digest"
  | "runtime_model_id"
  | "display_name_only";

export type JobKind =
  | "runtime_refresh"
  | "model_pull"
  | "build_analysis"
  | "build"
  | "image_export"
  | "image_import";

export type JobStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";

export interface RuntimeMetadata {
  identified_vendor?: string | null;
  registered?: boolean;
  server_running?: boolean;
  installed?: boolean;
  installed_not_running?: boolean;
  evidence?: string[];
  warnings?: string[];
  error?: string | null;
}

export interface RuntimeRead {
  id: number;
  kind: RuntimeKind;
  endpoint: string;
  source: RuntimeSource;
  status: RuntimeStatus;
  metadata_json: Record<string, unknown>;
  last_seen_at: string | null;
}

export interface ModelMetadata {
  display_name?: string | null;
  parameter_label?: string | null;
  trainability?: string | null;
  metadata_source?: string | null;
  runtime_id?: string | null;
}

export interface ModelRead {
  key: string;
  runtime_id: number;
  model_id: string;
  digest: string | null;
  family: string | null;
  format: string | null;
  quantization: string | null;
  parameter_count: number | null;
  size_bytes: number | null;
  context_length: number | null;
  capabilities_json: string[];
  identity_strength: ModelIdentityStrength;
  metadata_json: Record<string, unknown>;
  last_seen_at: string | null;
}

export interface JobRead {
  id: number;
  kind: JobKind;
  status: JobStatus;
  progress_0_1: number;
  phase: string;
  message: string;
  error_json: Record<string, unknown> | null;
}

export interface ModelFilters {
  runtime?: number;
  capability?: string;
  runnable?: boolean;
}

export interface RuntimeCreatePayload {
  kind: RuntimeKind;
  endpoint: string;
}

export interface ModelPullPayload {
  runtime_id: number;
  model_reference: string;
  expected_size_bytes?: number;
  user_approved: boolean;
  deadline_seconds?: number;
}

export interface RecoveryAction {
  code: string;
  message: string;
  optional: boolean;
}

export interface DiagnosticIssue {
  code: string;
  severity: string;
  message: string;
  recovery_actions: RecoveryAction[];
}

export interface Evidence {
  observed_source: string;
  value: string | number | boolean | null;
  basename: string | null;
  digest_prefix: string | null;
  boolean_presence: boolean | null;
  notes: string[];
}

export interface FeatureReadiness {
  feature: string;
  ready: boolean;
  blocks_core_start: boolean;
  blocks_feature_only: boolean;
  missing_reason: string;
}

export interface DiagnosticCheck {
  check_id: string;
  name: string;
  status: "pass" | "warn" | "fail" | "unavailable" | "skipped";
  severity: string;
  duration_seconds: number;
  observed_source: string;
  observed_at: string;
  evidence: Evidence;
  issues: DiagnosticIssue[];
  feature_readiness: FeatureReadiness[];
}

export interface DiagnosticReport {
  generated_at: string;
  budget: Record<string, unknown>;
  checks: DiagnosticCheck[];
  aggregate_health: "healthy" | "pass_with_limited_features" | "failed";
  total_duration_seconds: number;
  skipped_or_unavailable_count: number;
  error_count: number;
  details: Record<string, unknown>;
}

export interface CpuInfo {
  name: string | null;
  logical_cores: number | null;
  physical_cores: number | null;
}

export interface MemoryInfo {
  total_bytes: number | null;
  available_bytes: number | null;
}

export interface DiskInfo {
  path: string;
  total_bytes: number | null;
  used_bytes: number | null;
  free_bytes: number | null;
  error: string | null;
}

export interface AcceleratorInfo {
  kind: string;
  name: string | null;
  shared_memory: boolean | null;
  vram_total_bytes: number | null;
  vram_free_bytes: number | null;
  driver: string | null;
  detected_via: string | null;
}

export interface BackendAvailability {
  backend: string;
  role: "training" | "runtime";
  installed: boolean;
  detected_via: string | null;
  error: string | null;
}

export interface HardwareProfile {
  os: string;
  arch: string;
  cpu: CpuInfo;
  memory: MemoryInfo;
  disk: DiskInfo;
  accelerators: AcceleratorInfo[];
  training_backends: BackendAvailability[];
  runtime_backends: BackendAvailability[];
  collected_at: string;
  notes: string[];
}
