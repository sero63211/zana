import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import {
  CoreUnavailableError,
  fetchCoreHealth,
  restartCore,
} from "../api/core";

const CORE_HEALTH_QUERY_KEY = ["system", "core-health"] as const;

function describeError(error: unknown): { message: string; action: string } {
  if (error instanceof CoreUnavailableError) {
    return { message: error.message, action: error.recoveryAction };
  }
  return {
    message: "ZANA Core could not be reached.",
    action: "Retry the connection. If it still fails, restart the desktop app.",
  };
}

export function CoreStatus() {
  const [restartError, setRestartError] = useState<string | null>(null);
  const healthQuery = useQuery({
    queryKey: CORE_HEALTH_QUERY_KEY,
    queryFn: ({ signal }) => fetchCoreHealth(signal),
    retry: false,
    refetchInterval: 5_000,
    refetchIntervalInBackground: false,
  });

  const error = healthQuery.error ? describeError(healthQuery.error) : null;

  async function handleRetry() {
    setRestartError(null);
    try {
      await restartCore();
    } catch (restartFailure) {
      if (!(restartFailure instanceof CoreUnavailableError)) {
        setRestartError("Core could not be restarted automatically; retrying the connection instead.");
      }
    }
    await healthQuery.refetch();
  }

  if (healthQuery.isPending) {
    return (
      <section className="core-panel core-panel--checking" aria-live="polite">
        <span className="status-orbit" aria-hidden="true" />
        <div>
          <p className="eyebrow">Local system</p>
          <h2>Connecting to ZANA Core</h2>
          <p>Opening an authenticated loopback session. Nothing leaves this computer.</p>
        </div>
      </section>
    );
  }

  if (error || !healthQuery.data) {
    const failure = error ?? {
      message: "ZANA Core returned no health data.",
      action: "Restart ZANA Core and retry the connection.",
    };
    return (
      <section className="core-panel core-panel--error" aria-live="assertive">
        <span className="status-mark" aria-hidden="true">!</span>
        <div>
          <p className="eyebrow">Core unavailable</p>
          <h2>{failure.message}</h2>
          <p>{failure.action}</p>
          {restartError ? <p className="secondary-error">{restartError}</p> : null}
          <button
            type="button"
            className="primary-action"
            onClick={() => void handleRetry()}
            disabled={healthQuery.isFetching}
          >
            {healthQuery.isFetching ? "Retrying…" : "Restart and retry"}
          </button>
        </div>
      </section>
    );
  }

  const health = healthQuery.data;
  return (
    <section className="core-panel core-panel--healthy" aria-live="polite">
      <span className="status-mark" aria-hidden="true">✓</span>
      <div className="core-panel__body">
        <p className="eyebrow">Core connected</p>
        <h2>Your local build room is ready.</h2>
        <p>
          ZANA Core {health.version} is running as process {health.pid} on Python {health.python_version}. Uptime {health.uptime_seconds.toFixed(1)} seconds.
        </p>
        <dl className="truth-row">
          <div><dt>Boundary</dt><dd>127.0.0.1</dd></div>
          <div><dt>Authentication</dt><dd>Per launch</dd></div>
          <div><dt>Cloud account</dt><dd>Not required</dd></div>
        </dl>
      </div>
    </section>
  );
}
