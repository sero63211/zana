import { CoreStatus } from "../components/CoreStatus";
import { StateCard } from "../components/StateCard";
import { NAV_ITEMS } from "../navigation";

export function HomeView() {
  return (
    <div className="view-stack">
      <CoreStatus />

      <section className="section-block" aria-labelledby="section-title">
        <div className="section-heading">
          <p className="eyebrow">Workspace</p>
          <h2 id="section-title">Choose a workspace surface.</h2>
        </div>
        <div className="card-grid">
          {NAV_ITEMS.filter((item) => item.id !== "home").map((item) => (
            <a className="state-card state-card--link" href={item.href} key={item.id}>
              <StateCard icon={item.icon} title={item.label}>
                <p>{item.description}</p>
                <p className="card-meta">{item.homeState}</p>
              </StateCard>
            </a>
          ))}
        </div>
      </section>
    </div>
  );
}
