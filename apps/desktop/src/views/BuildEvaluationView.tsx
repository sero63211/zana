import { Icon } from "../icons";
import { StateCard } from "../components/StateCard";

export function BuildEvaluationView() {
  return (
    <div className="view-stack">
      <section className="section-block" aria-labelledby="build-title">
        <div className="section-heading">
          <p className="eyebrow">Build service</p>
          <h2 id="build-title">Isolated builds and evaluation gates</h2>
        </div>
        <p className="section-intro">
          Builds run specialized model work in an isolated job so failure cannot change source models or other builds. No build job can start until a runtime and capability exist.
        </p>
        <div className="card-grid">
          <StateCard icon="hammer" title="Build jobs">
            <p>No jobs are available because no capability or runtime has been connected.</p>
          </StateCard>
          <StateCard icon="shield" title="Isolation contract">
            <p>Builds will run with explicit user-controlled permission, scoped files, and no automatic network access.</p>
          </StateCard>
          <StateCard icon="check" title="Evaluation gates">
            <p>Gates run only against real measurements after a build produces an artifact.</p>
          </StateCard>
        </div>
      </section>

      <section className="safety-panel" aria-labelledby="safety-title">
        <span className="safety-panel__icon" aria-hidden="true"><Icon name="alert" size={18} /></span>
        <div>
          <p className="eyebrow">Before starting work</p>
          <h2 id="safety-title">Training and build actions are not enabled</h2>
          <p>
            This build room cannot start isolated training yet because no runtime, model, or capability is connected. When those exist, ZANA will still require you to explicitly start and stop each job; it will never auto-start training.
          </p>
          <div className="action-strip">
            <button type="button" className="primary-action" disabled title="A runtime and capability must be connected first">
              <Icon name="hammer" size={16} />
              Start isolated build
            </button>
            <span className="action-note">Unavailable until a runtime, capability, and evaluation gate are connected.</span>
          </div>
        </div>
      </section>
    </div>
  );
}
