import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithClient } from "../test/render";
import { HomeView } from "./HomeView";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

const CONNECTION = {
  baseUrl: "http://127.0.0.1:59421",
  token: "launch-token",
  launchError: null,
};

const PROFILE = {
  os: "macos",
  arch: "arm64",
  cpu: { name: "Apple M2 Pro", logical_cores: 12, physical_cores: 8 },
  memory: { total_bytes: 16_000_000_000, available_bytes: 8_000_000_000 },
  disk: { path: "/Users/sero/Library/Application Support/ZANA", total_bytes: 100_000_000_000, used_bytes: 50_000_000_000, free_bytes: 50_000_000_000, error: null },
  accelerators: [],
  training_backends: [],
  runtime_backends: [],
  collected_at: "2026-08-10T12:00:00Z",
  notes: [],
};

const DOCTOR = {
  generated_at: "2026-08-10T12:00:00Z",
  budget: {},
  checks: [],
  aggregate_health: "healthy",
  total_duration_seconds: 0.2,
  skipped_or_unavailable_count: 0,
  error_count: 0,
  details: {},
};

function stubCoreFetch(handlers: Record<string, () => Response>) {
  vi.mocked(fetch).mockImplementation((input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : "";
    const handler = Object.entries(handlers).find(([prefix]) => url.includes(prefix))?.[1];
    if (!handler) return Promise.resolve(jsonResponse({ error: { code: "NOT_FOUND", message: "missing fixture", actions: [] } }, 404));
    return Promise.resolve(handler());
  });
}

describe("HomeView", () => {
  beforeEach(() => {
    Object.defineProperty(window, "__TAURI_INTERNALS__", { value: {}, configurable: true });
    invoke.mockReset();
    invoke.mockResolvedValue(CONNECTION);
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
    vi.unstubAllGlobals();
  });

  it("shows an honest loading state", async () => {
    vi.mocked(fetch).mockReturnValue(new Promise<Response>(() => undefined));
    renderWithClient(<HomeView />);
    expect(await screen.findByText(/Reading the live runtime, model, hardware, and doctor status/)).toBeInTheDocument();
  });

  it("summarizes real runtime, model, hardware, and doctor data", async () => {
    stubCoreFetch({
      "/api/v1/system/profile": () => jsonResponse(PROFILE),
      "/api/v1/system/doctor": () => jsonResponse(DOCTOR),
      "/api/v1/runtimes": () => jsonResponse([
        {
          id: 1,
          kind: "ollama",
          endpoint: "http://127.0.0.1:11434",
          source: "auto",
          status: "online",
          metadata_json: { installed_not_running: false, registered: true },
          last_seen_at: null,
        },
        {
          id: 2,
          kind: "ollama",
          endpoint: "http://127.0.0.1:11435",
          source: "auto",
          status: "unknown",
          metadata_json: { installed_not_running: true },
          last_seen_at: null,
        },
      ]),
      "/api/v1/models": () => jsonResponse([
        modelFixture("qwen2:0.5b", "qwen", "sha256:abc"),
        modelFixture("qwen2:1.5b", "qwen", "sha256:def"),
        modelFixture("llama3.2:1b", "llama", null),
      ]),
      "/api/v1/health": () => jsonResponse({ status: "ok", version: "0.1.0", python_version: "3.12.10", pid: 1, uptime_seconds: 1 }),
    });

    renderWithClient(<HomeView />);

    await screen.findByText("Healthy");
    expect(document.body.textContent).toContain("2 configured");
    expect(document.body.textContent).toContain("3 exposed by runtimes");
    expect(await screenFind("Healthy")).toBeTruthy();
    expect(await screenFind("Manage models")).toBeTruthy();
    expect(document.querySelector('a[href="#/settings-doctor"]')).not.toBeNull();
  });

  it("gives actionable empty-state guidance when nothing is discovered", async () => {
    stubCoreFetch({
      "/api/v1/system/profile": () => jsonResponse(PROFILE),
      "/api/v1/system/doctor": () => jsonResponse(DOCTOR),
      "/api/v1/runtimes": () => jsonResponse([]),
      "/api/v1/models": () => jsonResponse([]),
      "/api/v1/health": () => jsonResponse({ status: "ok", version: "0.1.0", python_version: "3.12.10", pid: 1, uptime_seconds: 1 }),
    });

    renderWithClient(<HomeView />);

    expect(await screenFind("No local runtime has been detected")).toBeTruthy();
    expect(screenFindText("add a manual endpoint in Models")).toBeTruthy();
  });

  it("shows a recovery error instead of fake dashboard numbers", async () => {
    stubCoreFetch({
      "/api/v1/system/profile": () => jsonResponse(PROFILE),
      "/api/v1/system/doctor": () => jsonResponse({ error: { code: "DOCTOR_FAILED", message: "Doctor probe failed.", recoverable: true, actions: ["retry_doctor"] } }, 500),
      "/api/v1/runtimes": () => jsonResponse([]),
      "/api/v1/models": () => jsonResponse([]),
      "/api/v1/health": () => jsonResponse({ status: "ok", version: "0.1.0", python_version: "3.12.10", pid: 1, uptime_seconds: 1 }),
    });

    renderWithClient(<HomeView />);

    expect(await screenFind("Could not load local model status")).toBeTruthy();
    expect(await screenFind("Retry summary")).toBeTruthy();
  });
});

function modelFixture(modelId: string, family: string, digest: string | null) {
  return {
    key: `1:${modelId}`,
    runtime_id: 1,
    model_id: modelId,
    digest,
    family,
    format: "gguf",
    quantization: "Q4_K_M",
    parameter_count: 1_000_000_000,
    size_bytes: 1_000_000_000,
    context_length: 32768,
    capabilities_json: ["completion"],
    identity_strength: digest ? "exact_digest" : "display_name_only",
    metadata_json: { display_name: modelId, parameter_label: "1B", trainability: "unknown", metadata_source: "runtime" },
    last_seen_at: "2026-08-10T12:00:00Z",
  };
}

async function screenFind(text: string): Promise<HTMLElement> {
  return screen.findByText(text);
}

function screenFindText(text: string): boolean {
  return document.body.textContent?.includes(text) ?? false;
}
