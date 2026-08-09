import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithClient } from "../test/render";
import { RuntimesModelsView } from "./RuntimesModelsView";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

const CONNECTION = {
  baseUrl: "http://127.0.0.1:59421",
  token: "launch-token",
  launchError: null,
};

function runtimeFixture(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1,
    kind: "ollama",
    endpoint: "http://127.0.0.1:11434",
    source: "auto",
    status: "unknown",
    metadata_json: { installed_not_running: true, registered: true },
    last_seen_at: null,
    ...overrides,
  };
}

function modelFixture(modelId: string) {
  return {
    key: `1:${modelId}`,
    runtime_id: 1,
    model_id: modelId,
    digest: "sha256:abc",
    family: "qwen",
    format: "gguf",
    quantization: "Q4_K_M",
    parameter_count: 1_000_000_000,
    size_bytes: 1_073_741_824,
    context_length: 32768,
    capabilities_json: ["completion"],
    identity_strength: "exact_digest",
    metadata_json: { display_name: modelId, parameter_label: "1B", trainability: "unknown", metadata_source: "runtime" },
    last_seen_at: "2026-08-10T12:00:00Z",
  };
}

type FetchFixture = (url: string, init?: RequestInit) => Response;

function stubCoreFetch(handler: FetchFixture) {
  vi.mocked(fetch).mockImplementation((input, init) =>
    Promise.resolve(handler(typeof input === "string" ? input : input instanceof URL ? input.href : "", init)),
  );
}

describe("RuntimesModelsView", () => {
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

  it("shows a real loading state while the catalog is read", async () => {
    vi.mocked(fetch).mockReturnValue(new Promise<Response>(() => undefined));
    renderWithClient(<RuntimesModelsView />);
    expect(await screen.findByText("Reading the live runtime and model catalog from ZANA Core.")).toBeInTheDocument();
  });

  it("renders real runtime records, model descriptors, and returned metadata", async () => {
    stubCoreFetch((url) => {
      if (url.includes("/api/v1/runtimes")) return jsonResponse([runtimeFixture()]);
      if (url.includes("/api/v1/models")) return jsonResponse([modelFixture("qwen2:0.5b")]);
      return jsonResponse([]);
    });

    renderWithClient(<RuntimesModelsView />);

    expect(await screen.findByRole("heading", { name: /Ollama #1/ })).toBeInTheDocument();
    expect(await screen.findByText("qwen2:0.5b")).toBeInTheDocument();
    expect(screen.getByText("Installed, not running")).toBeInTheDocument();
    expect(screen.getByText("Exact digest")).toBeInTheDocument();
    expect(screen.getByText("1.00 GB")).toBeInTheDocument();
    expect(screen.getByText("Full returned metadata")).toBeInTheDocument();
  });

  it("shows empty-state guidance and an honest pull reason when no Ollama runtime exists", async () => {
    stubCoreFetch((url) => {
      if (url.includes("/api/v1/runtimes")) return jsonResponse([]);
      if (url.includes("/api/v1/models")) return jsonResponse([]);
      return jsonResponse([]);
    });

    renderWithClient(<RuntimesModelsView />);

    expect(await screen.findByText("No runtimes or models yet")).toBeInTheDocument();
    expect(await screen.findByText(/Pulling requires an Ollama runtime record/)).toBeInTheDocument();
  });

  it("refreshes discovery and invalidates runtimes and models", async () => {
    let runtimesCall = 0;
    let modelsCall = 0;
    stubCoreFetch((url, init) => {
      if (url.includes("/api/v1/runtimes/refresh")) {
        expect(init?.method).toBe("POST");
        return jsonResponse({
          id: 5,
          kind: "runtime_refresh",
          status: "SUCCEEDED",
          progress_0_1: 1,
          phase: "complete",
          message: "Runtime discovery complete; 1 candidate(s) probed.",
          error_json: null,
        });
      }
      if (url.includes("/api/v1/runtimes")) {
        runtimesCall += 1;
        if (runtimesCall === 1) {
          return jsonResponse([runtimeFixture()]);
        }
        return jsonResponse([runtimeFixture({ status: "online", metadata_json: { installed_not_running: false, registered: true } })]);
      }
      if (url.includes("/api/v1/models")) {
        modelsCall += 1;
        if (modelsCall === 1) {
          return jsonResponse([modelFixture("old-model")]);
        }
        return jsonResponse([modelFixture("new-model")]);
      }
      return jsonResponse([]);
    });

    renderWithClient(<RuntimesModelsView />);
    expect(await screen.findByText("old-model")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Discover runtimes" }));

    expect(await screen.findByText("Job #5 succeeded")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("new-model")).toBeInTheDocument());
    expect(screen.getByText("Online")).toBeInTheDocument();
    expect(runtimesCall).toBeGreaterThan(1);
    expect(modelsCall).toBeGreaterThan(1);
  });

  it("adds a manual runtime with the adapter kind and endpoint payload", async () => {
    let runtimesCall = 0;
    stubCoreFetch((url, init) => {
      if (url.includes("/api/v1/runtimes/manual") && init?.method === "POST") {
        const body = JSON.parse(typeof init.body === "string" ? init.body : "{}") as Record<string, string>;
        expect(body).toEqual({ kind: "openai-compatible", endpoint: "http://127.0.0.1:1234" });
        return jsonResponse(runtimeFixture({
          id: 9,
          kind: "openai-compatible",
          endpoint: "http://127.0.0.1:1234",
          source: "manual",
          status: "unknown",
          metadata_json: {},
        }), 201);
      }
      if (url.includes("/api/v1/runtimes")) {
        runtimesCall += 1;
        if (runtimesCall === 1) return jsonResponse([]);
        return jsonResponse([
          runtimeFixture({ id: 9, kind: "openai-compatible", endpoint: "http://127.0.0.1:1234", source: "manual", status: "unknown", metadata_json: {} }),
        ]);
      }
      return jsonResponse([]);
    });

    renderWithClient(<RuntimesModelsView />);
    await userEvent.selectOptions(screen.getByLabelText("Adapter / runtime kind"), "openai-compatible");
    await userEvent.type(screen.getByLabelText("Endpoint URL"), "http://127.0.0.1:1234");
    await userEvent.click(screen.getByRole("button", { name: "Add manual runtime" }));

    expect(await screen.findByRole("heading", { name: /OpenAI-compatible #9/ })).toBeInTheDocument();
    expect(screen.getByText("Manual, not probed")).toBeInTheDocument();
  });

  it("deletes only manually configured runtimes after explicit confirmation", async () => {
    let runtimesCall = 0;
    stubCoreFetch((url, init) => {
      if (url.includes("/api/v1/runtimes/2") && init?.method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      if (url.includes("/api/v1/runtimes")) {
        runtimesCall += 1;
        if (runtimesCall === 1) {
          return jsonResponse([
            runtimeFixture({ id: 1, source: "auto", endpoint: "http://127.0.0.1:11434" }),
            runtimeFixture({ id: 2, source: "manual", endpoint: "http://127.0.0.1:1234", kind: "openai-compatible", metadata_json: {} }),
          ]);
        }
        return jsonResponse([runtimeFixture({ id: 1, source: "auto", endpoint: "http://127.0.0.1:11434" })]);
      }
      return jsonResponse([]);
    });

    renderWithClient(<RuntimesModelsView />);
    const deleteButton = await screen.findByRole("button", { name: "Remove manual runtime 2" });
    await userEvent.click(deleteButton);
    await userEvent.click(screen.getByRole("button", { name: "Confirm removal of manual runtime 2" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: "Remove manual runtime 2" })).toBeNull());
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true);
  });

  it("requires the deliberate approval control before queueing a pull", async () => {
    let pullCall = 0;
    stubCoreFetch((url, init) => {
      if (url.includes("/api/v1/models/pull")) {
        pullCall += 1;
        const body = JSON.parse(typeof init?.body === "string" ? init.body : "{}") as Record<string, unknown>;
        expect(body).toMatchObject({
          runtime_id: 1,
          model_reference: "qwen2.5:0.5b",
          user_approved: true,
          expected_size_bytes: 1000,
          deadline_seconds: 60,
        });
        return jsonResponse({
          id: 7,
          kind: "model_pull",
          status: "PENDING",
          progress_0_1: 0,
          phase: "queued",
          message: "qwen2.5:0.5b",
          error_json: { code: "ACQUISITION_QUEUED", message: "Native model acquisition queued; not started.", actions: [] },
        }, 201);
      }
      if (url.includes("/api/v1/runtimes")) {
        return jsonResponse([runtimeFixture({ status: "online", metadata_json: { installed_not_running: false, registered: true } })]);
      }
      return jsonResponse([]);
    });

    renderWithClient(<RuntimesModelsView />);

    const submit = await screen.findByRole("button", { name: "Queue approved pull" });
    expect(submit).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Model reference"), "qwen2.5:0.5b");
    await userEvent.type(screen.getByLabelText("Expected size (bytes, optional)"), "1000");
    await userEvent.type(screen.getByLabelText("Deadline (seconds, optional)"), "60");
    expect(screen.getByRole("button", { name: "Queue approved pull" })).toBeDisabled();

    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Queue approved pull" }));

    expect(await screen.findByText("Job #7 pending")).toBeInTheDocument();
    expect(screen.getByText(/no bytes were downloaded and no model was installed/)).toBeInTheDocument();
    expect(pullCall).toBe(1);
  });

  it("renders a canonical recovery error instead of a fake catalog", async () => {
    stubCoreFetch(() => jsonResponse({
      error: {
        code: "RUNTIME_REFRESH_FAILED",
        message: "Runtime discovery could not complete.",
        recoverable: true,
        actions: ["retry_refresh"],
      },
    }, 500));

    renderWithClient(<RuntimesModelsView />);

    expect(await screen.findByText("Could not load runtimes or models")).toBeInTheDocument();
    expect(await screen.findByText(/retry_refresh/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });
});
