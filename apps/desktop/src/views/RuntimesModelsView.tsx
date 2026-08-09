import { Icon } from "../icons";
import { StateCard } from "../components/StateCard";
import { useCoreHealth } from "../hooks/useCoreHealth";

export function RuntimesModelsView() {
  const healthQuery = useCoreHealth();

  return (
    <div className="view-stack">
      <section className="section-block" aria-labelledby="runtimes-title">
        <div className="section-heading">
          <p className="eyebrow">Discovery</p>
          <h2 id="runtimes-title">Local runtimes and exposed models</h2>
        </div>
        <p className="section-intro">
          ZANA will ask runtimes installed on this computer which models they actually expose. No local runtime has been connected yet, so there is nothing to list.
        </p>
        <div className="card-grid">
          <StateCard icon="cpu" title="Runtime adapters">
            <p>None are connected. Add a supported local runtime before discovery can return records.</p>
          </StateCard>
          <StateCard icon="layers" title="Model catalog">
            <p>Unavailable until a runtime is connected and a real probe completes.</p>
          </StateCard>
          <StateCard icon="shield" title="Isolation">
            <p>Model runs will stay inside the Core loopback boundary and require explicit local permission.</p>
          </StateCard>
        </div>
      </section>

      <section className="state-panel state-panel--neutral" aria-labelledby="runtime-status-title">
        <div className="state-panel__heading">
          <p className="eyebrow">Runtime status</p>
          <h2 id="runtime-status-title">Nothing discovered yet</h2>
        </div>
        <p>
          Core health is {healthQuery.isPending ? "being checked" : healthQuery.error ? "unavailable" : "connected"}. Discovery results will appear here only after a runtime probe succeeds.
        </p>
        <div className="action-strip">
          <button type="button" className="primary-action" disabled title="No runtime adapter is installed">
            <Icon name="refresh" size={16} />
            Discover runtimes
          </button>
          <span className="action-note">No adapter is connected, so discovery cannot run yet.</span>
        </div>
      </section>
    </div>
  );
}
