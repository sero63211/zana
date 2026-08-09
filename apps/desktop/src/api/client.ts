import { resolveCoreConnection } from "./core";
import type {
  DiagnosticCheck,
  DiagnosticReport,
  HardwareProfile,
  JobRead,
  ModelFilters,
  ModelPullPayload,
  ModelRead,
  RuntimeCreatePayload,
  RuntimeKind,
  RuntimeRead,
  RuntimeSource,
  RuntimeStatus,
} from "./types";

export class CoreApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: unknown;
  readonly recoverable: boolean;
  readonly actions: readonly string[];

  constructor(
    code: string,
    message: string,
    status: number,
    options: { details?: unknown; recoverable?: boolean; actions?: readonly string[] } = {},
  ) {
    super(message);
    this.name = "CoreApiError";
    this.code = code;
    this.status = status;
    this.details = options.details ?? null;
    this.recoverable = options.recoverable ?? false;
    this.actions = options.actions ?? [];
  }
}

class ResponseValidationError extends Error {
  constructor(label: string, detail: string) {
    super(`${label} is not a valid ZANA Core response: ${detail}`);
    this.name = "ResponseValidationError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string") {
    throw new ResponseValidationError(field, "expected a string");
  }
  return value;
}

function requireNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ResponseValidationError(field, "expected a finite number");
  }
  return value;
}

function requireBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new ResponseValidationError(field, "expected a boolean");
  }
  return value;
}

function optionalString(value: unknown, field: string): string | null {
  if (value === null || value === undefined) return null;
  return requireString(value, field);
}

function optionalNumber(value: unknown, field: string): number | null {
  if (value === null || value === undefined) return null;
  return requireNumber(value, field);
}

function optionalBoolean(value: unknown, field: string): boolean | null {
  if (value === null || value === undefined) return null;
  return requireBoolean(value, field);
}

function requireStringArray(value: unknown, field: string): string[] {
  if (!Array.isArray(value)) {
    throw new ResponseValidationError(field, "expected an array");
  }
  return value.map((item, index) => requireString(item, `${field}[${index}]`));
}

function requireRecord(value: unknown, field: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new ResponseValidationError(field, "expected an object");
  }
  return value;
}

function optionalRecord(value: unknown, field: string): Record<string, unknown> | null {
  if (value === null || value === undefined) return null;
  return requireRecord(value, field);
}

function optionalArray(value: unknown, field: string): unknown[] | null {
  if (value === null || value === undefined) return null;
  if (!Array.isArray(value)) {
    throw new ResponseValidationError(field, "expected an array");
  }
  return value as unknown[];
}

const RUNTIME_KINDS: ReadonlySet<string> = new Set([
  "ollama",
  "lm-studio",
  "llama.cpp",
  "mlx-lm",
  "openai-compatible",
  "unknown",
]);

const RUNTIME_SOURCES: ReadonlySet<string> = new Set(["auto", "manual"]);

const RUNTIME_STATUSES: ReadonlySet<string> = new Set(["unknown", "online", "offline", "error"]);

const JOB_KINDS: ReadonlySet<string> = new Set([
  "runtime_refresh",
  "model_pull",
  "build_analysis",
  "build",
  "image_export",
  "image_import",
]);

const JOB_STATUSES: ReadonlySet<string> = new Set([
  "PENDING",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
]);

function parseRuntimeMetadata(value: unknown): Record<string, unknown> {
  const record = requireRecord(value, "runtime.metadata_json");
  const result: Record<string, unknown> = {};
  if (typeof record.identified_vendor === "string" || record.identified_vendor === null) {
    result.identified_vendor = record.identified_vendor;
  }
  for (const key of ["registered", "server_running", "installed", "installed_not_running"]) {
    if (typeof record[key] === "boolean") result[key] = record[key];
  }
  if (Array.isArray(record.evidence)) {
    result.evidence = record.evidence.filter((item): item is string => typeof item === "string");
  }
  if (Array.isArray(record.warnings)) {
    result.warnings = record.warnings.filter((item): item is string => typeof item === "string");
  }
  if (typeof record.error === "string" || record.error === null) result.error = record.error;
  return result;
}

function parseRuntime(value: unknown): RuntimeRead {
  const record = requireRecord(value, "runtime");
  const kind = requireString(record.kind, "runtime.kind");
  const source = requireString(record.source, "runtime.source");
  const status = requireString(record.status, "runtime.status");
  if (!RUNTIME_KINDS.has(kind)) {
    throw new ResponseValidationError("runtime.kind", "unsupported runtime kind");
  }
  if (!RUNTIME_SOURCES.has(source)) {
    throw new ResponseValidationError("runtime.source", "unsupported runtime source");
  }
  if (!RUNTIME_STATUSES.has(status)) {
    throw new ResponseValidationError("runtime.status", "unsupported runtime status");
  }
  return {
    id: requireNumber(record.id, "runtime.id"),
    kind: kind as RuntimeKind,
    endpoint: requireString(record.endpoint, "runtime.endpoint"),
    source: source as RuntimeSource,
    status: status as RuntimeStatus,
    metadata_json: parseRuntimeMetadata(record.metadata_json),
    last_seen_at: optionalString(record.last_seen_at, "runtime.last_seen_at"),
  };
}

function parseModelMetadata(value: unknown): Record<string, unknown> {
  const record = requireRecord(value, "model.metadata_json");
  const result: Record<string, unknown> = {};
  for (const key of ["display_name", "parameter_label", "trainability", "metadata_source", "runtime_id"]) {
    if (typeof record[key] === "string" || record[key] === null) result[key] = record[key];
  }
  return result;
}

function parseModel(value: unknown): ModelRead {
  const record = requireRecord(value, "model");
  const identityStrength = requireString(record.identity_strength, "model.identity_strength");
  if (!["unknown", "exact_digest", "runtime_model_id", "display_name_only"].includes(identityStrength)) {
    throw new ResponseValidationError("model.identity_strength", "unsupported identity strength");
  }
  return {
    key: requireString(record.key, "model.key"),
    runtime_id: requireNumber(record.runtime_id, "model.runtime_id"),
    model_id: requireString(record.model_id, "model.model_id"),
    digest: optionalString(record.digest, "model.digest"),
    family: optionalString(record.family, "model.family"),
    format: optionalString(record.format, "model.format"),
    quantization: optionalString(record.quantization, "model.quantization"),
    parameter_count: optionalNumber(record.parameter_count, "model.parameter_count"),
    size_bytes: optionalNumber(record.size_bytes, "model.size_bytes"),
    context_length: optionalNumber(record.context_length, "model.context_length"),
    capabilities_json: requireStringArray(record.capabilities_json, "model.capabilities_json"),
    identity_strength: identityStrength as ModelRead["identity_strength"],
    metadata_json: parseModelMetadata(record.metadata_json),
    last_seen_at: optionalString(record.last_seen_at, "model.last_seen_at"),
  };
}

function parseJob(value: unknown): JobRead {
  const record = requireRecord(value, "job");
  const kind = requireString(record.kind, "job.kind");
  const status = requireString(record.status, "job.status");
  if (!JOB_KINDS.has(kind)) {
    throw new ResponseValidationError("job.kind", "unsupported job kind");
  }
  if (!JOB_STATUSES.has(status)) {
    throw new ResponseValidationError("job.status", "unsupported job status");
  }
  return {
    id: requireNumber(record.id, "job.id"),
    kind: kind as JobRead["kind"],
    status: status as JobRead["status"],
    progress_0_1: requireNumber(record.progress_0_1, "job.progress_0_1"),
    phase: requireString(record.phase, "job.phase"),
    message: requireString(record.message, "job.message"),
    error_json: optionalRecord(record.error_json, "job.error_json"),
  };
}

function parseRecoveryAction(value: unknown) {
  const record = requireRecord(value, "recovery_action");
  return {
    code: requireString(record.code, "recovery_action.code"),
    message: requireString(record.message, "recovery_action.message"),
    optional: requireBoolean(record.optional, "recovery_action.optional"),
  };
}

function parseEvidence(value: unknown) {
  const record = requireRecord(value, "evidence");
  const notes = optionalArray(record.notes, "evidence.notes");
  let scalarValue: string | number | boolean | null = null;
  if (record.value !== null && record.value !== undefined) {
    if (typeof record.value === "string" || typeof record.value === "number" || typeof record.value === "boolean") {
      scalarValue = record.value;
    } else {
      throw new ResponseValidationError("evidence.value", "expected a scalar");
    }
  }
  return {
    observed_source: requireString(record.observed_source, "evidence.observed_source"),
    value: scalarValue,
    basename: optionalString(record.basename, "evidence.basename"),
    digest_prefix: optionalString(record.digest_prefix, "evidence.digest_prefix"),
    boolean_presence: optionalBoolean(record.boolean_presence, "evidence.boolean_presence"),
    notes: notes === null ? [] : notes.filter((item): item is string => typeof item === "string"),
  };
}

function parseIssue(value: unknown) {
  const record = requireRecord(value, "issue");
  const actions = optionalArray(record.recovery_actions, "issue.recovery_actions");
  return {
    code: requireString(record.code, "issue.code"),
    severity: requireString(record.severity, "issue.severity"),
    message: requireString(record.message, "issue.message"),
    recovery_actions: actions === null ? [] : actions.map(parseRecoveryAction),
  };
}

function parseFeatureReadiness(value: unknown) {
  const record = requireRecord(value, "feature_readiness");
  return {
    feature: requireString(record.feature, "feature_readiness.feature"),
    ready: requireBoolean(record.ready, "feature_readiness.ready"),
    blocks_core_start: requireBoolean(record.blocks_core_start, "feature_readiness.blocks_core_start"),
    blocks_feature_only: requireBoolean(record.blocks_feature_only, "feature_readiness.blocks_feature_only"),
    missing_reason: requireString(record.missing_reason, "feature_readiness.missing_reason"),
  };
}

function parseCheck(value: unknown) {
  const record = requireRecord(value, "check");
  const status = requireString(record.status, "check.status");
  if (!["pass", "warn", "fail", "unavailable", "skipped"].includes(status)) {
    throw new ResponseValidationError("check.status", "unsupported check status");
  }
  const issues = optionalArray(record.issues, "check.issues");
  const readiness = optionalArray(record.feature_readiness, "check.feature_readiness");
  return {
    check_id: requireString(record.check_id, "check.check_id"),
    name: requireString(record.name, "check.name"),
    status: status as DiagnosticCheck["status"],
    severity: requireString(record.severity, "check.severity"),
    duration_seconds: requireNumber(record.duration_seconds, "check.duration_seconds"),
    observed_source: requireString(record.observed_source, "check.observed_source"),
    observed_at: requireString(record.observed_at, "check.observed_at"),
    evidence: parseEvidence(record.evidence),
    issues: issues === null ? [] : issues.map(parseIssue),
    feature_readiness: readiness === null ? [] : readiness.map(parseFeatureReadiness),
  };
}

function parseDoctorReport(value: unknown): DiagnosticReport {
  const record = requireRecord(value, "doctor");
  const aggregate = requireString(record.aggregate_health, "doctor.aggregate_health");
  if (!["healthy", "pass_with_limited_features", "failed"].includes(aggregate)) {
    throw new ResponseValidationError("doctor.aggregate_health", "unsupported aggregate health");
  }
  const checks = optionalArray(record.checks, "doctor.checks");
  return {
    generated_at: requireString(record.generated_at, "doctor.generated_at"),
    budget: requireRecord(record.budget, "doctor.budget"),
    checks: checks === null ? [] : checks.map(parseCheck),
    aggregate_health: aggregate as DiagnosticReport["aggregate_health"],
    total_duration_seconds: requireNumber(record.total_duration_seconds, "doctor.total_duration_seconds"),
    skipped_or_unavailable_count: requireNumber(record.skipped_or_unavailable_count, "doctor.skipped_or_unavailable_count"),
    error_count: requireNumber(record.error_count, "doctor.error_count"),
    details: requireRecord(record.details, "doctor.details"),
  };
}

function parseHardwareProfile(value: unknown): HardwareProfile {
  const record = requireRecord(value, "hardware_profile");
  const cpu = requireRecord(record.cpu, "hardware_profile.cpu");
  const memory = requireRecord(record.memory, "hardware_profile.memory");
  const disk = requireRecord(record.disk, "hardware_profile.disk");
  const accelerators = optionalArray(record.accelerators, "hardware_profile.accelerators");
  const training = optionalArray(record.training_backends, "hardware_profile.training_backends");
  const runtime = optionalArray(record.runtime_backends, "hardware_profile.runtime_backends");
  const notes = optionalArray(record.notes, "hardware_profile.notes");

  function parseAccelerator(item: unknown) {
    const value = requireRecord(item, "accelerator");
    return {
      kind: requireString(value.kind, "accelerator.kind"),
      name: optionalString(value.name, "accelerator.name"),
      shared_memory: optionalBoolean(value.shared_memory, "accelerator.shared_memory"),
      vram_total_bytes: optionalNumber(value.vram_total_bytes, "accelerator.vram_total_bytes"),
      vram_free_bytes: optionalNumber(value.vram_free_bytes, "accelerator.vram_free_bytes"),
      driver: optionalString(value.driver, "accelerator.driver"),
      detected_via: optionalString(value.detected_via, "accelerator.detected_via"),
    };
  }

  function parseBackend(item: unknown): HardwareProfile["runtime_backends"][number] {
    const value = requireRecord(item, "backend");
    const role = requireString(value.role, "backend.role");
    if (role !== "runtime" && role !== "training") {
      throw new ResponseValidationError("backend.role", "unsupported backend role");
    }
    return {
      backend: requireString(value.backend, "backend.backend"),
      role,
      installed: requireBoolean(value.installed, "backend.installed"),
      detected_via: optionalString(value.detected_via, "backend.detected_via"),
      error: optionalString(value.error, "backend.error"),
    };
  }

  return {
    os: requireString(record.os, "hardware_profile.os"),
    arch: requireString(record.arch, "hardware_profile.arch"),
    cpu: {
      name: optionalString(cpu.name, "hardware_profile.cpu.name"),
      logical_cores: optionalNumber(cpu.logical_cores, "hardware_profile.cpu.logical_cores"),
      physical_cores: optionalNumber(cpu.physical_cores, "hardware_profile.cpu.physical_cores"),
    },
    memory: {
      total_bytes: optionalNumber(memory.total_bytes, "hardware_profile.memory.total_bytes"),
      available_bytes: optionalNumber(memory.available_bytes, "hardware_profile.memory.available_bytes"),
    },
    disk: {
      path: requireString(disk.path, "hardware_profile.disk.path"),
      total_bytes: optionalNumber(disk.total_bytes, "hardware_profile.disk.total_bytes"),
      used_bytes: optionalNumber(disk.used_bytes, "hardware_profile.disk.used_bytes"),
      free_bytes: optionalNumber(disk.free_bytes, "hardware_profile.disk.free_bytes"),
      error: optionalString(disk.error, "hardware_profile.disk.error"),
    },
    accelerators: accelerators === null ? [] : accelerators.map(parseAccelerator),
    training_backends: training === null ? [] : training.map(parseBackend),
    runtime_backends: runtime === null ? [] : runtime.map(parseBackend),
    collected_at: requireString(record.collected_at, "hardware_profile.collected_at"),
    notes: notes === null ? [] : notes.filter((item): item is string => typeof item === "string"),
  };
}

export function encodePathSegment(value: string): string {
  return value
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
}

export function encodeModelKey(key: string): string {
  return encodePathSegment(key);
}

export function buildQuery(filters: ModelFilters): string {
  const search = new URLSearchParams();
  if (filters.runtime !== undefined && filters.runtime !== null) {
    search.set("runtime", String(filters.runtime));
  }
  if (filters.capability !== undefined && filters.capability !== "") {
    search.set("capability", filters.capability);
  }
  if (filters.runnable !== undefined && filters.runnable !== null) {
    search.set("runnable", String(filters.runnable));
  }
  const query = search.toString();
  return query === "" ? "" : `?${query}`;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

async function readErrorPayload(response: Response): Promise<unknown> {
  try {
    return (await response.json()) as unknown;
  } catch {
    return null;
  }
}

async function toCoreApiError(response: Response): Promise<CoreApiError> {
  const payload = await readErrorPayload(response);
  if (isRecord(payload) && isRecord(payload.error)) {
    const error = payload.error;
    return new CoreApiError(
      typeof error.code === "string" ? error.code : "HTTP_ERROR",
      typeof error.message === "string" ? error.message : `ZANA Core returned HTTP ${response.status}.`,
      response.status,
      {
        details: isRecord(error.details) ? error.details : null,
        recoverable: typeof error.recoverable === "boolean" ? error.recoverable : false,
        actions: Array.isArray(error.actions)
          ? error.actions.filter((item): item is string => typeof item === "string")
          : [],
      },
    );
  }
  return new CoreApiError("HTTP_ERROR", `ZANA Core returned HTTP ${response.status}.`, response.status, {
    recoverable: response.status >= 500 ? false : true,
    actions: response.status === 401 ? ["restart_desktop_app"] : ["retry_request"],
  });
}

async function requestCore(
  path: string,
  init: {
    method?: "GET" | "POST" | "DELETE";
    body?: unknown;
    signal?: AbortSignal;
  } = {},
): Promise<Response> {
  const connection = await resolveCoreConnection();
  const base = connection.baseUrl.endsWith("/") ? connection.baseUrl : `${connection.baseUrl}/`;
  const url = new URL(path, base);
  const headers: Record<string, string> = {
    Authorization: `Bearer ${connection.token}`,
    Accept: "application/json",
  };
  const requestInit: RequestInit = { method: init.method ?? "GET", headers, signal: init.signal };
  if (init.body !== undefined) {
    headers["Content-Type"] = "application/json";
    requestInit.body = JSON.stringify(init.body);
  }

  try {
    return await fetch(url, requestInit);
  } catch (error) {
    if (isAbortError(error)) throw error;
    const cause = error instanceof Error ? error.message : "connection failed";
    throw new CoreApiError("CORE_UNREACHABLE", `ZANA Core is not reachable (${cause}).`, 0, {
      recoverable: true,
      actions: ["restart_core", "retry_request"],
    });
  }
}

async function requestJson<T>(
  path: string,
  init: {
    method?: "GET" | "POST";
    body?: unknown;
    signal?: AbortSignal;
  },
  parse: (value: unknown) => T,
  label: string,
): Promise<T> {
  const response = await requestCore(path, init);
  if (!response.ok) {
    throw await toCoreApiError(response);
  }
  let payload: unknown;
  try {
    payload = (await response.json()) as unknown;
  } catch {
    throw new CoreApiError("INVALID_RESPONSE", `${label} returned a non-JSON response.`, response.status, {
      recoverable: true,
      actions: ["retry_request"],
    });
  }
  try {
    return parse(payload);
  } catch (error) {
    if (error instanceof ResponseValidationError) {
      throw new CoreApiError("INVALID_RESPONSE", error.message, response.status, {
        recoverable: true,
        actions: ["retry_request"],
      });
    }
    throw error;
  }
}

export async function fetchSystemProfile(signal?: AbortSignal): Promise<HardwareProfile> {
  return requestJson("/api/v1/system/profile", { signal }, parseHardwareProfile, "System profile");
}

export async function fetchSystemDoctor(signal?: AbortSignal): Promise<DiagnosticReport> {
  return requestJson("/api/v1/system/doctor", { signal }, parseDoctorReport, "System doctor");
}

export async function fetchRuntimes(signal?: AbortSignal): Promise<RuntimeRead[]> {
  return requestJson("/api/v1/runtimes", { signal }, (value) => {
    if (!Array.isArray(value)) {
      throw new ResponseValidationError("runtimes", "expected an array");
    }
    return value.map(parseRuntime);
  }, "Runtimes");
}

export async function refreshRuntimes(signal?: AbortSignal): Promise<JobRead> {
  return requestJson("/api/v1/runtimes/refresh", { method: "POST", signal }, parseJob, "Runtime refresh");
}

export async function addRuntime(payload: RuntimeCreatePayload, signal?: AbortSignal): Promise<RuntimeRead> {
  return requestJson(
    "/api/v1/runtimes/manual",
    { method: "POST", body: payload, signal },
    parseRuntime,
    "Manual runtime",
  );
}

export async function deleteRuntime(runtimeId: number, signal?: AbortSignal): Promise<void> {
  const response = await requestCore(`/api/v1/runtimes/${encodeURIComponent(runtimeId)}`, {
    method: "DELETE",
    signal,
  });
  if (!response.ok) {
    throw await toCoreApiError(response);
  }
}

export async function fetchModels(filters: ModelFilters = {}, signal?: AbortSignal): Promise<ModelRead[]> {
  return requestJson(`/api/v1/models${buildQuery(filters)}`, { signal }, (value) => {
    if (!Array.isArray(value)) {
      throw new ResponseValidationError("models", "expected an array");
    }
    return value.map(parseModel);
  }, "Models");
}

export async function fetchModel(modelKey: string, signal?: AbortSignal): Promise<ModelRead> {
  return requestJson(`/api/v1/models/${encodeModelKey(modelKey)}`, { signal }, parseModel, "Model detail");
}

export async function pullModel(payload: ModelPullPayload, signal?: AbortSignal): Promise<JobRead> {
  return requestJson("/api/v1/models/pull", { method: "POST", body: payload, signal }, parseJob, "Model pull");
}
