import { invoke } from "@tauri-apps/api/core";

export interface CoreConnection {
  baseUrl: string;
  token: string;
  launchError: string | null;
}

export interface CoreHealth {
  status: "ok";
  version: string;
  python_version: string;
  pid: number;
  uptime_seconds: number;
}

export class CoreUnavailableError extends Error {
  readonly recoveryAction: string;

  constructor(message: string, recoveryAction: string) {
    super(message);
    this.name = "CoreUnavailableError";
    this.recoveryAction = recoveryAction;
  }
}

function isTauriRuntime(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

function readWebDevelopmentConnection(): CoreConnection {
  const baseUrl = import.meta.env.VITE_ZANA_API_BASE?.trim();
  const token = import.meta.env.VITE_ZANA_API_TOKEN?.trim();

  if (!baseUrl || !token) {
    throw new CoreUnavailableError(
      "Core connection is not configured for this browser session.",
      "Launch the Tauri app, or set VITE_ZANA_API_BASE and VITE_ZANA_API_TOKEN for local web development.",
    );
  }

  return { baseUrl, token, launchError: null };
}

export async function resolveCoreConnection(): Promise<CoreConnection> {
  if (!isTauriRuntime()) {
    return readWebDevelopmentConnection();
  }

  const connection = await invoke<CoreConnection>("core_connection");
  if (connection.launchError) {
    throw new CoreUnavailableError(connection.launchError, "Restart ZANA Core and try again.");
  }
  if (!connection.baseUrl || !connection.token) {
    throw new CoreUnavailableError(
      "ZANA Core did not provide a usable loopback connection.",
      "Restart ZANA Core and try again.",
    );
  }
  return connection;
}

export async function fetchCoreHealth(signal?: AbortSignal): Promise<CoreHealth> {
  const connection = await resolveCoreConnection();
  let response: Response;

  try {
    response = await fetch(`${connection.baseUrl}/api/v1/health`, {
      method: "GET",
      headers: { Authorization: `Bearer ${connection.token}` },
      cache: "no-store",
      signal,
    });
  } catch (error) {
    const cause = error instanceof Error ? error.message : "connection failed";
    throw new CoreUnavailableError(
      `ZANA Core is not reachable on this computer (${cause}).`,
      "Check the Core process, then retry the connection.",
    );
  }

  if (!response.ok) {
    throw new CoreUnavailableError(
      `ZANA Core returned HTTP ${response.status}.`,
      response.status === 401
        ? "Restart the desktop app to rotate the local API token."
        : "Retry the connection. If it still fails, inspect Core logs in Settings.",
    );
  }

  const health = (await response.json()) as Partial<CoreHealth>;
  if (
    health.status !== "ok" ||
    typeof health.version !== "string" ||
    typeof health.python_version !== "string" ||
    typeof health.pid !== "number" ||
    typeof health.uptime_seconds !== "number"
  ) {
    throw new CoreUnavailableError(
      "The loopback service did not return a valid ZANA Core health response.",
      "Stop the conflicting service and restart ZANA.",
    );
  }

  return health as CoreHealth;
}

export async function restartCore(): Promise<void> {
  if (!isTauriRuntime()) {
    throw new CoreUnavailableError(
      "Core restart is available only in the desktop app.",
      "Start Core manually for browser development, then retry.",
    );
  }
  await invoke("restart_core");
}
