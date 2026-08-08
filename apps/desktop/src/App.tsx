import { CoreStatus } from "./components/CoreStatus";
import "./styles/app.css";

const NAVIGATION = ["Home", "Models", "Capabilities", "Images", "Instances", "Evaluations", "Settings"] as const;

export function App() {
  return (
    <div className="app-frame">
      <aside className="sidebar" aria-label="ZANA navigation">
        <div className="brand-block">
          <span className="brand-sigil" aria-hidden="true">Z</span>
          <div>
            <p className="brand-name">ZANA</p>
            <p className="brand-note">Local capability studio</p>
          </div>
        </div>

        <nav>
          <ul className="nav-list">
            {NAVIGATION.map((item) => (
              <li key={item} className={item === "Home" ? "nav-item nav-item--active" : "nav-item nav-item--future"}>
                <span>{item}</span>
                {item === "Home" ? <span className="nav-indicator" aria-label="Current page" /> : <span className="nav-soon">Later</span>}
              </li>
            ))}
          </ul>
        </nav>

        <div className="privacy-note">
          <span className="privacy-dot" aria-hidden="true" />
          <div><strong>Local mode</strong><span>Telemetry is off</span></div>
        </div>
      </aside>

      <main className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Foundation check</p>
            <h1>Build knowledge you can inspect.</h1>
          </div>
          <p className="header-copy">
            ZANA turns models already under your control into verified, portable AI instances.
          </p>
        </header>

        <CoreStatus />

        <section className="principles" aria-labelledby="principles-title">
          <div className="principles-heading">
            <p className="eyebrow">Operating contract</p>
            <h2 id="principles-title">Trust is built into the lifecycle.</h2>
          </div>
          <ol>
            <li><span>01</span><div><strong>Discover</strong><p>Ask local runtimes what models they actually expose.</p></div></li>
            <li><span>02</span><div><strong>Measure</strong><p>Capture a real baseline before specialization begins.</p></div></li>
            <li><span>03</span><div><strong>Verify</strong><p>Promote only when declared evaluation gates pass.</p></div></li>
          </ol>
        </section>
      </main>
    </div>
  );
}
