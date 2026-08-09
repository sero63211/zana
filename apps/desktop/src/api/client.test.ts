import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  buildQuery,
  CoreApiError,
  encodeModelKey,
  fetchModels,
  fetchRuntimes,
  fetchSystemDoctor,
  fetchSystemProfile,
} from "./client";
import { fetchCoreHealth } from "./core";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

const CONNECTION = {
  baseUrl: "http://127.0.0.1:59421",
  token: "launch-token",
  launchError: null,
};

function mockFetch(response: Response) {
  vi.mocked(fetch).mockResolvedValue(response);
}

describe("Core API client", () => {
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

  it("encodes query filters and omits empty filters", () => {
    expect(buildQuery({ runtime: 3, capability: "completion", runnable: true })).toBe(
      "?runtime=3&capability=completion&runnable=true",
    );
    expect(buildQuery({})).toBe("");
    expect(buildQuery({ capability: "", runtime: undefined, runnable: undefined })).toBe("");
  });

  it("encodes path segments safely without breaking model keys", () => {
    expect(encodeModelKey("qwen2/1.5b")).toBe("qwen2/1.5b");
    expect(encodeModelKey("model with spaces/tag")).toBe("model%20with%20spaces/tag");
    expect(encodeModelKey("a/b?c=d")).toBe("a/b%3Fc%3Dd");
  });

  it("validates real runtime data and sends the per-launch token", async () => {
    mockFetch(jsonFixture([
      {
        id: 1,
        kind: "ollama",
        endpoint: "http://127.0.0.1:11434",
        source: "auto",
        status: "online",
        metadata_json: { installed_not_running: false, registered: true },
        last_seen_at: "2026-08-10T12:00:00Z",
      },
    ]));

    const runtimes = await fetchRuntimes();

    expect(runtimes).toHaveLength(1);
    expect(runtimes[0].kind).toBe("ollama");
    const call = vi.mocked(fetch).mock.calls[0];
    expect(callUrl(call[0])).toBe("http://127.0.0.1:59421/api/v1/runtimes");
    expect((call[1]?.headers as Record<string, string>).Authorization).toBe("Bearer launch-token");
  });

  it("parses canonical error envelopes with recovery actions", async () => {
    mockFetch(jsonFixture({
      error: {
        code: "USER_APPROVAL_REQUIRED",
        message: "Native model acquisition requires explicit user approval.",
        details: {},
        recoverable: true,
        actions: ["confirm_model_download"],
      },
    }, 422));

    await expect(fetchRuntimes()).rejects.toMatchObject({
      code: "USER_APPROVAL_REQUIRED",
      recoverable: true,
      actions: ["confirm_model_download"],
    });
  });

  it("never leaks the token into error messages", async () => {
    mockFetch(jsonFixture({
      error: {
        code: "HTTP_ERROR",
        message: "failure",
        recoverable: false,
        actions: [],
      },
    }, 500));

    try {
      await fetchRuntimes();
      throw new Error("expected rejection");
    } catch (error) {
      expect(error).toBeInstanceOf(CoreApiError);
      expect(String(error)).not.toContain("launch-token");
    }
  });

  it("rejects malformed untrusted JSON instead of rendering it", async () => {
    mockFetch(jsonFixture([{ id: "not-a-number" }]));

    await expect(fetchRuntimes()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });

  it("builds model filter queries on the real endpoint", async () => {
    mockFetch(jsonFixture([]));
    await fetchModels({ runtime: 2, capability: "completion" });
    const call = vi.mocked(fetch).mock.calls[0];
    expect(callUrl(call[0])).toBe("http://127.0.0.1:59421/api/v1/models?runtime=2&capability=completion");
    expect(call[1]?.method).toBe("GET");
  });

  it("sanitizes transport failures from the typed client", async () => {
    invoke.mockResolvedValue({
      baseUrl: "http://127.0.0.1:59421",
      token: "hostile-token-value",
      launchError: null,
    });
    vi.mocked(fetch).mockRejectedValue(
      new Error("fetch failed at /private/var/zana/host-path?token=hostile-token-value"),
    );

    try {
      await fetchRuntimes();
      throw new Error("expected rejection");
    } catch (error) {
      expect(error).toBeInstanceOf(CoreApiError);
      const rendered = `${String(error)} ${JSON.stringify(error)}`;
      expect(rendered).not.toContain("hostile-token-value");
      expect(rendered).not.toContain("/private/var/zana/host-path");
      expect(rendered).not.toContain("fetch failed at");
    }
  });

  it("sanitizes transport failures from Core health and preserves AbortError", async () => {
    invoke.mockResolvedValue({
      baseUrl: "http://127.0.0.1:59421",
      token: "hostile-token-value",
      launchError: null,
    });
    vi.mocked(fetch).mockRejectedValue(
      new Error("boom at C:\\Users\\zana\\host\\path token=hostile-token-value"),
    );

    try {
      await fetchCoreHealth();
      throw new Error("expected rejection");
    } catch (error) {
      const rendered = String(error);
      expect(rendered).toContain("ZANA Core is not reachable on this computer.");
      expect(rendered).not.toContain("hostile-token-value");
      expect(rendered).not.toContain("C:\\Users\\zana\\host\\path");
      expect(rendered).not.toContain("boom at");
    }

    vi.mocked(fetch).mockRejectedValue(new DOMException("aborted", "AbortError"));
    await expect(fetchCoreHealth()).rejects.toMatchObject({ name: "AbortError" });
  });

  it("fails closed when canonical doctor arrays are missing", async () => {
    mockFetch(jsonFixture({
      generated_at: "2026-08-10T12:00:00Z",
      budget: {},
      aggregate_health: "healthy",
      total_duration_seconds: 0,
      skipped_or_unavailable_count: 0,
      error_count: 0,
      details: {},
    }));

    await expect(fetchSystemDoctor()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });

  it("fails closed when canonical hardware arrays are missing", async () => {
    mockFetch(jsonFixture({
      os: "macos",
      arch: "arm64",
      cpu: {},
      memory: {},
      disk: { path: "/tmp/zana" },
      collected_at: "2026-08-10T12:00:00Z",
      notes: [],
    }));

    await expect(fetchSystemProfile()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });
});

function jsonFixture(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function callUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return "";
}
