import { Icon } from "../icons";
import { StateCard } from "../components/StateCard";

export function ImagesView() {
  return (
    <div className="view-stack">
      <section className="section-block" aria-labelledby="images-title">
        <div className="section-heading">
          <p className="eyebrow">Artifacts</p>
          <h2 id="images-title">Portable ZANA Images</h2>
        </div>
        <p className="section-intro">
          A ZANA Image captures a verified capability so it can be moved between computers and run as an instance. The image store is not connected, so there are no images to inspect.
        </p>
        <div className="card-grid">
          <StateCard icon="image" title="Image store">
            <p>Empty. Exporting is possible only after a real build produces a verified artifact.</p>
          </StateCard>
          <StateCard icon="lock" title="Export safety">
            <p>ZANA will require explicit confirmation and never export the Core token or local credentials.</p>
          </StateCard>
          <StateCard icon="shield" title="Verification">
            <p>Images are promoted only when their evaluation gates pass against real measurements.</p>
          </StateCard>
        </div>
        <div className="action-strip">
          <button type="button" className="primary-action" disabled title="No verified image exists">
            <Icon name="image" size={16} />
            Export image
          </button>
          <span className="action-note">Nothing can be exported until a real build produces a verified artifact.</span>
        </div>
      </section>
    </div>
  );
}
