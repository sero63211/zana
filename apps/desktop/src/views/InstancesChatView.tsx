import { Icon } from "../icons";
import { StateCard } from "../components/StateCard";

export function InstancesChatView() {
  return (
    <div className="view-stack">
      <section className="section-block" aria-labelledby="instances-title">
        <div className="section-heading">
          <p className="eyebrow">Runtime</p>
          <h2 id="instances-title">Local instances and chat</h2>
        </div>
        <p className="section-intro">
          Instances are running copies of a ZANA Image with a chat session. No instance exists until an image is imported and explicitly started.
        </p>
        <div className="card-grid">
          <StateCard icon="message" title="Chat sessions">
            <p>No sessions are open because no instance is running.</p>
          </StateCard>
          <StateCard icon="cpu" title="Running instances">
            <p>None. Instances will appear here only after a real image is selected and launched.</p>
          </StateCard>
          <StateCard icon="lock" title="Explicit control">
            <p>Instances start and stop only when you choose; nothing runs in the background without your action.</p>
          </StateCard>
        </div>
        <div className="action-strip">
          <button type="button" className="primary-action" disabled title="No image has been imported">
            <Icon name="message" size={16} />
            Start instance
          </button>
          <span className="action-note">Instances start and stop only when you choose, after an image exists.</span>
        </div>
      </section>
    </div>
  );
}
