import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithClient } from "../test/render";
import { SettingsDoctorView } from "./SettingsDoctorView";

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
  accelerators: [{ kind: "apple_metal", name: "Apple M2 Pro", shared_memory: true, vram_total_bytes: null, vram_free_bytes: null, driver: null, detected_via: "platform" }],
  training_backends: [{ backend: "mlx", role: "training", installed: false, detected_via: "probe", error: null }],
  runtime_backends: [{ backend: "ollama", role: "runtime", installed: true, detected_via: "executable", error: null }],
  collected_at: "2026-08-10T12:00:00Z",
  notes: [],
};

const DOCTOR = {
  generated_at: "2026-08-10T12:00:00Z",
  budget: {},
  checks: [
    {
      check_id: "sqlite",
      name: "SQLite reachability",
      status: "pass",
      severity: "info",
      duration_seconds: 0.01,
      observed_source: "pragma",
      observed_at: "2026-08-10T12:00:00Z",
      evidence: {
        observed_source: "sqlite",
        value: null,
        basename: "zana.sqlite3",
        digest_prefix: null,
        boolean_presence: true,
        notes: ["journal_mode=wal"],
      },
      issues: [],
      feature_readiness: [],
    },
    {
      check_id: "runtimes",
      name: "Runtime discovery",
      status: "fail",
      severity: "error",
      duration_seconds: 0.1,
      observed_source: "registry",
      observed_at: "2026-08-10T12:00:00Z",
      evidence: {
        observed_source: "registry",
        value: null,
        basename: null,
        digest_prefix: null,
        boolean_presence: false,
        notes: [],
      },
      issues: [
        {
          code: "NO_RUNTIME",
          severity: "error",
          message: "No local runtime is running.",
          recovery_actions: [
            { code: "start_runtime", message: "Start a supported local runtime.", optional: false },
          ],
        },
      ],
      feature_readiness: [
        {
          feature: "ollama",
          ready: false,
          blocks_core_start: false,
          blocks_feature_only: true,
          missing_reason: "server not running",
        },
      ],
    },
  ],
  aggregate_health: "failed",
  total_duration_seconds: 0.2,
  skipped_or_unavailable_count: 0,
  error_count: 1,
  details: {},
};

function stubCoreFetch(handler: (url: string) => Response) {
  vi.mocked(fetch).mockImplementation((input) =>
    Promise.resolve(handler(typeof input === "string" ? input : input instanceof URL ? input.href : "")),
  );
}

describe("SettingsDoctorView", () => {
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
    renderWithClient(<SettingsDoctorView />);
    expect(await screen.findByText("Running the bounded doctor probes and reading the hardware profile.")).toBeInTheDocument();
  });

  it("renders the aggregate health, every check, evidence, issues, and safe recovery actions", async () => {
    stubCoreFetch((url) => {
      if (url.includes("/api/v1/system/doctor")) return jsonResponse(DOCTOR);
      if (url.includes("/api/v1/system/profile")) return jsonResponse(PROFILE);
      return jsonResponse([]);
    });

    renderWithClient(<SettingsDoctorView />);

    expect(await screen.findByText("Needs attention")).toBeInTheDocument();
    expect(screen.getByText("SQLite reachability")).toBeInTheDocument();
    expect(screen.getByText("Runtime discovery")).toBeInTheDocument();
    expect(screen.getByText("journal_mode=wal")).toBeInTheDocument();
    expect(screen.getByText("NO_RUNTIME")).toBeInTheDocument();
    expect(screen.getByText(/start_runtime: Start a supported local runtime/)).toBeInTheDocument();
    expect(document.body.textContent).toContain("ollama · runtime · installed");
    expect(screen.getAllByText("Apple M2 Pro").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/macos · arm64/)).toBeInTheDocument();
  });

  it("never renders the launch token", async () => {
    stubCoreFetch((url) => {
      if (url.includes("/api/v1/system/doctor")) return jsonResponse(DOCTOR);
      if (url.includes("/api/v1/system/profile")) return jsonResponse(PROFILE);
      return jsonResponse([]);
    });

    renderWithClient(<SettingsDoctorView />);
    await screen.findByText("Needs attention");

    expect(document.body.textContent).not.toContain("launch-token");
  });

  it("shows a recovery error instead of fabricated diagnostics", async () => {
    stubCoreFetch(() => jsonResponse({
      error: {
        code: "DOCTOR_FAILED",
        message: "Doctor probes could not complete.",
        recoverable: true,
        actions: ["retry_doctor"],
      },
    }, 500));

    renderWithClient(<SettingsDoctorView />);

    expect(await screen.findByText("Could not load the doctor report")).toBeInTheDocument();
    expect(await screen.findByText(/retry_doctor/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry diagnostics" })).toBeEnabled();
  });
});
