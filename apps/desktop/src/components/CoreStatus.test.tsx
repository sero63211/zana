import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CoreStatus } from "./CoreStatus";

const invoke = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("CoreStatus", () => {
  beforeEach(() => {
    Object.defineProperty(window, "__TAURI_INTERNALS__", { value: {}, configurable: true });
    invoke.mockReset();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
    vi.unstubAllGlobals();
  });

  it("shows the authenticated Core process when health is real", async () => {
    invoke.mockResolvedValue({ baseUrl: "http://127.0.0.1:59421", token: "launch-token", launchError: null });
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({
      status: "ok", version: "0.1.0", python_version: "3.12.10", pid: 4242, uptime_seconds: 1.25,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    render(<CoreStatus />, { wrapper });

    expect(await screen.findByText("Your local build room is ready.")).toBeInTheDocument();
    expect(screen.getByText(/process 4242/)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:59421/api/v1/health",
      expect.objectContaining({ headers: { Authorization: "Bearer launch-token" } }),
    );
  });

  it("shows an actionable failure instead of fake healthy data", async () => {
    invoke.mockResolvedValue({ baseUrl: "http://127.0.0.1:59421", token: "launch-token", launchError: "Core binary was not found." });
    render(<CoreStatus />, { wrapper });
    expect(await screen.findByText("Core binary was not found.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart and retry" })).toBeEnabled();
  });

  it("restarts Core and retries health", async () => {
    invoke
      .mockResolvedValueOnce({ baseUrl: "http://127.0.0.1:59421", token: "launch-token", launchError: "Core stopped." })
      .mockResolvedValueOnce(undefined)
      .mockResolvedValue({ baseUrl: "http://127.0.0.1:59421", token: "launch-token", launchError: null });
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({
      status: "ok", version: "0.1.0", python_version: "3.12.10", pid: 99, uptime_seconds: 2,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    render(<CoreStatus />, { wrapper });
    await userEvent.click(await screen.findByRole("button", { name: "Restart and retry" }));
    await waitFor(() => expect(screen.getByText("Your local build room is ready.")).toBeInTheDocument());
    expect(invoke).toHaveBeenCalledWith("restart_core");
  });
});
