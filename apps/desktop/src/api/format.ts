import type { ModelRead, RuntimeMetadata, RuntimeRead } from "./types";

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || value < 0) return "Not reported";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = value;
  let unit = "B";
  for (const candidate of units) {
    if (amount < 1024) break;
    amount /= 1024;
    unit = candidate;
  }
  return `${amount >= 10 || unit === "B" ? amount.toFixed(1) : amount.toFixed(2)} ${unit}`;
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Not reported";
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 2 }).format(value);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "Not reported";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not reported";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function basename(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}

export function runtimeKindLabel(kind: RuntimeRead["kind"]): string {
  switch (kind) {
    case "ollama":
      return "Ollama";
    case "lm-studio":
      return "LM Studio";
    case "llama.cpp":
      return "llama.cpp";
    case "mlx-lm":
      return "MLX LM";
    case "openai-compatible":
      return "OpenAI-compatible";
    default:
      return "Unknown";
  }
}

export function runtimeMetadata(runtime: RuntimeRead): RuntimeMetadata {
  const meta = runtime.metadata_json;
  return {
    identified_vendor: typeof meta.identified_vendor === "string" ? meta.identified_vendor : null,
    registered: typeof meta.registered === "boolean" ? meta.registered : undefined,
    server_running: typeof meta.server_running === "boolean" ? meta.server_running : undefined,
    installed: typeof meta.installed === "boolean" ? meta.installed : undefined,
    installed_not_running: typeof meta.installed_not_running === "boolean" ? meta.installed_not_running : undefined,
    evidence: Array.isArray(meta.evidence)
      ? meta.evidence.filter((item): item is string => typeof item === "string")
      : undefined,
    warnings: Array.isArray(meta.warnings)
      ? meta.warnings.filter((item): item is string => typeof item === "string")
      : undefined,
    error: typeof meta.error === "string" ? meta.error : null,
  };
}

export type RuntimeTone = "online" | "offline" | "error" | "idle" | "unknown" | "manual";

export function runtimeStatus(runtime: RuntimeRead): { label: string; tone: RuntimeTone } {
  const meta = runtimeMetadata(runtime);
  if (meta.installed_not_running) return { label: "Installed, not running", tone: "idle" };
  switch (runtime.status) {
    case "online":
      return { label: "Online", tone: "online" };
    case "offline":
      return { label: "Offline", tone: "offline" };
    case "error":
      return { label: "Error", tone: "error" };
    default:
      if (runtime.source === "manual") return { label: "Manual, not probed", tone: "manual" };
      return { label: "Unknown", tone: "unknown" };
  }
}

export function identityStrengthLabel(strength: ModelRead["identity_strength"]): string {
  switch (strength) {
    case "exact_digest":
      return "Exact digest";
    case "runtime_model_id":
      return "Runtime model id";
    case "display_name_only":
      return "Display name only";
    default:
      return "Identity unknown";
  }
}

export function jobStatusLabel(status: string): string {
  switch (status) {
    case "SUCCEEDED":
      return "succeeded";
    case "FAILED":
      return "failed";
    case "RUNNING":
      return "running";
    case "CANCELLED":
      return "cancelled";
    default:
      return "pending";
  }
}

export function modelDisplayName(model: ModelRead): string {
  const meta = model.metadata_json;
  return typeof meta.display_name === "string" && meta.display_name !== "" ? meta.display_name : model.model_id;
}
