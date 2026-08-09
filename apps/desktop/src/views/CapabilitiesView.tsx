import { Icon } from "../icons";
import { StateCard } from "../components/StateCard";

export function CapabilitiesView() {
  return (
    <div className="view-stack">
      <section className="section-block" aria-labelledby="capabilities-title">
        <div className="section-heading">
          <p className="eyebrow">Registry</p>
          <h2 id="capabilities-title">Reusable capability packages</h2>
        </div>
        <p className="section-intro">
          Capabilities capture a model, prompt, evaluation gates, and provenance as one portable unit. The registry is not connected, so no capability records exist.
        </p>
        <div className="card-grid">
          <StateCard icon="layers" title="Capability catalog">
            <p>Empty. Created capabilities will be listed here once build and evaluation can persist real records.</p>
          </StateCard>
          <StateCard icon="shield" title="Provenance">
            <p>Every capability will record its source model, image, and gate results before it can be reused.</p>
          </StateCard>
          <StateCard icon="lock" title="Local ownership">
            <p>Registry data is stored on this computer and never uploaded automatically.</p>
          </StateCard>
        </div>
        <div className="action-strip">
          <button type="button" className="primary-action" disabled title="The registry is not connected">
            <Icon name="plus" size={16} />
            Create capability
          </button>
          <span className="action-note">Creation unlocks after build and evaluation can persist real records.</span>
        </div>
      </section>
    </div>
  );
}
