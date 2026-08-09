import { useState } from "react";

import {
  CoreUnavailableError,
  restartCore,
} from "../api/core";
import { Icon } from "../icons";
import { useCoreHealth } from "../hooks/useCoreHealth";
import { StatusPanel } from "./StatusPanel";

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
  const healthQuery = useCoreHealth();
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
      <StatusPanel tone="loading" eyebrow="Local system" title="Connecting to ZANA Core">
        <p>Opening an authenticated loopback session. Nothing leaves this computer.</p>
      </StatusPanel>
    );
  }

  if (error || !healthQuery.data) {
    const failure = error ?? {
      message: "ZANA Core returned no health data.",
      action: "Restart ZANA Core and retry the connection.",
    };
    return (
      <StatusPanel tone="error" eyebrow="Core unavailable" title={failure.message}>
        <p>{failure.action}</p>
        {restartError ? <p className="secondary-error">{restartError}</p> : null}
        <button
          type="button"
          className="primary-action"
          onClick={() => void handleRetry()}
          disabled={healthQuery.isFetching}
        >
          <Icon name="refresh" size={16} />
          {healthQuery.isFetching ? "Retrying…" : "Restart and retry"}
        </button>
      </StatusPanel>
    );
  }

  const health = healthQuery.data;
  return (
    <StatusPanel tone="healthy" eyebrow="Core connected" title="Your local build room is ready.">
      <p>
        ZANA Core {health.version} is running as process {health.pid} on Python {health.python_version}. Uptime {health.uptime_seconds.toFixed(1)} seconds.
      </p>
      <dl className="truth-row">
        <div><dt>Boundary</dt><dd>127.0.0.1</dd></div>
        <div><dt>Authentication</dt><dd>Per launch</dd></div>
        <div><dt>Cloud account</dt><dd>Not required</dd></div>
      </dl>
    </StatusPanel>
  );
}
