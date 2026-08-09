import { CoreApiError } from "../api/client";
import { formatBytes, runtimeMetadata } from "../api/format";
import { CoreStatus } from "../components/CoreStatus";
import { StateCard } from "../components/StateCard";
import { useModels, useRuntimes, useSystemDoctor, useSystemProfile } from "../hooks/useCoreApi";
import { Icon } from "../icons";

function describeError(error: unknown): { message: string; actions: string } {
  if (error instanceof CoreApiError) {
    return {
      message: error.message,
      actions: error.actions.length > 0 ? error.actions.join(", ") : "Retry the request.",
    };
  }
  return {
    message: "ZANA Core could not provide the local model status.",
    actions: "Retry the request. If it still fails, restart the desktop app.",
  };
}

function doctorLabel(health: "healthy" | "pass_with_limited_features" | "failed" | undefined): string {
  switch (health) {
    case "healthy":
      return "Healthy";
    case "pass_with_limited_features":
      return "Running with limitations";
    case "failed":
      return "Needs attention";
    default:
      return "Not measured yet";
  }
}

export function HomeView() {
  const runtimesQuery = useRuntimes();
  const modelsQuery = useModels();
  const profileQuery = useSystemProfile();
  const doctorQuery = useSystemDoctor();

  const isLoading = [runtimesQuery, modelsQuery, profileQuery, doctorQuery].some((query) => query.isPending);
  const firstError = [runtimesQuery, modelsQuery, profileQuery, doctorQuery].find((query) => query.error)?.error ?? null;

  const runtimes = runtimesQuery.data ?? [];
  const models = modelsQuery.data ?? [];
  const profile = profileQuery.data;
  const doctor = doctorQuery.data;

  const onlineRuntimes = runtimes.filter((runtime) => runtime.status === "online").length;
  const idleRuntimes = runtimes.filter((runtime) => runtimeMetadata(runtime).installed_not_running).length;
  const manualRuntimes = runtimes.filter((runtime) => runtime.source === "manual").length;
  const familyCount = new Set(models.map((model) => model.family).filter(Boolean)).size;
  const digestCount = models.filter((model) => model.digest).length;

  function retryAll() {
    void runtimesQuery.refetch();
    void modelsQuery.refetch();
    void profileQuery.refetch();
    void doctorQuery.refetch();
  }

  const runtimeMeta = onlineRuntimes > 0 || idleRuntimes > 0
    ? `${onlineRuntimes} online, ${idleRuntimes} installed but not running${manualRuntimes > 0 ? `, ${manualRuntimes} manual` : ""}`
    : manualRuntimes > 0
      ? `${manualRuntimes} manual endpoint${manualRuntimes === 1 ? "" : "s"}`
      : "No runtime records yet";

  return (
    <div className="view-stack">
      <CoreStatus />

      <section className="section-block" aria-labelledby="home-summary-title">
        <div className="section-heading">
          <p className="eyebrow">Local model readiness</p>
          <h2 id="home-summary-title">Detected runtimes and models</h2>
        </div>

        {isLoading ? (
          <div className="loading-panel" role="status" aria-live="polite">
            <span className="loading-panel__mark" aria-hidden="true"><Icon name="refresh" size={16} /></span>
            <div>
              <p className="eyebrow">Loading</p>
              <p>Reading the live runtime, model, hardware, and doctor status from ZANA Core.</p>
            </div>
          </div>
        ) : firstError ? (
          <div className="status-panel status-panel--error" role="alert">
            <span className="status-mark" aria-hidden="true"><Icon name="alert" size={18} /></span>
            <div className="status-panel__body">
              <p className="eyebrow">Summary unavailable</p>
              <h2>Could not load local model status</h2>
              <div className="status-panel__copy">
                <p>{describeError(firstError).message}</p>
                <p className="action-note">Recovery: {describeError(firstError).actions}</p>
              </div>
              <div className="status-panel__actions">
                <button type="button" className="primary-action" onClick={retryAll}>
                  <Icon name="refresh" size={16} />
                  Retry summary
                </button>
              </div>
            </div>
          </div>
        ) : (
          <>
            {runtimes.length === 0 && models.length === 0 ? (
              <div className="state-panel state-panel--neutral" role="status">
                <p className="eyebrow">Nothing discovered yet</p>
                <h3>No local runtime has been detected</h3>
                <p>
                  Start a supported runtime, add a manual endpoint in Models, then run discovery.
                  Models can be listed only after a real probe returns them.
                </p>
              </div>
            ) : null}

            <div className="dashboard-grid">
              <StateCard icon="cpu" title="Local runtimes">
                <p><strong>{runtimes.length}</strong> configured</p>
                <p className="card-meta">{runtimeMeta}</p>
              </StateCard>
              <StateCard icon="layers" title="Models detected">
                <p><strong>{models.length}</strong> exposed by runtimes</p>
                <p className="card-meta">{familyCount} families, {digestCount} with digest</p>
              </StateCard>
              <StateCard icon="settings" title="Hardware">
                <p>{profile ? `${profile.os} · ${profile.arch}` : "Profile not reported"}</p>
                <p className="card-meta">
                  {profile
                    ? `${profile.cpu.logical_cores ?? "Unknown"} cores · ${formatBytes(profile.memory.total_bytes)} RAM · ${formatBytes(profile.disk.free_bytes)} free`
                    : "Not reported"}
                </p>
              </StateCard>
              <StateCard icon="shield" title="Doctor readiness">
                <p>{doctorLabel(doctor?.aggregate_health)}</p>
                <p className="card-meta">
                  {doctor ? `${doctor.checks.length} checks, ${doctor.error_count} errors` : "Not reported"}
                </p>
              </StateCard>
            </div>

            <div className="action-strip">
              <a className="primary-action" href="#/runtimes-models">
                <Icon name="cpu" size={16} />
                Manage models
              </a>
              <a className="secondary-action" href="#/settings-doctor">
                <Icon name="settings" size={16} />
                Run doctor
              </a>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
