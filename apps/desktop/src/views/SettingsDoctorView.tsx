import { useCoreHealth } from "../hooks/useCoreHealth";
import { Icon } from "../icons";

export function SettingsDoctorView() {
  const healthQuery = useCoreHealth();

  const checks: Array<{ label: string; detail: string; state: "pass" | "pending" | "fail" | "not-run" }> = [
    { label: "Core process", detail: healthQuery.data ? `Process ${healthQuery.data.pid}` : "Not verified", state: healthQuery.data ? "pass" : healthQuery.isPending ? "pending" : "fail" },
    { label: "Loopback boundary", detail: healthQuery.data ? "127.0.0.1 only" : "Not verified", state: healthQuery.data ? "pass" : healthQuery.isPending ? "pending" : "not-run" },
    { label: "Per-launch token", detail: healthQuery.data ? "Rotated on launch" : "Not verified", state: healthQuery.data ? "pass" : healthQuery.isPending ? "pending" : "not-run" },
    { label: "Runtime discovery", detail: "No local runtime connected", state: "not-run" },
    { label: "Capability registry", detail: "No registry connected", state: "not-run" },
    { label: "Disk headroom", detail: "Not measured by desktop", state: "not-run" },
  ];

  return (
    <div className="view-stack">
      <section className="section-block" aria-labelledby="settings-title">
        <div className="section-heading">
          <p className="eyebrow">Configuration</p>
          <h2 id="settings-title">Settings and diagnostics</h2>
        </div>
        <p className="section-intro">
          This desktop build keeps all configuration local. No cloud account, telemetry, or persistence layer is enabled.
        </p>
        <div className="doctor-list">
          {checks.map((check) => (
            <article className="doctor-row" key={check.label}>
              <span className={`doctor-state doctor-state--${check.state}`} aria-label={check.state}>
                {check.state === "pass" ? <Icon name="check" size={14} /> : check.state === "fail" || check.state === "not-run" ? <Icon name="info" size={14} /> : <Icon name="refresh" size={14} />}
              </span>
              <div>
                <h3>{check.label}</h3>
                <p>{check.detail}</p>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="safety-panel safety-panel--quiet" aria-labelledby="token-safety-title">
        <span className="safety-panel__icon" aria-hidden="true"><Icon name="lock" size={18} /></span>
        <div>
          <p className="eyebrow">Credential safety</p>
          <h2 id="token-safety-title">The Core token stays in memory</h2>
          <p>
            The per-launch loopback token is held only by the desktop process and never rendered, persisted, or logged. Diagnostics report status, never credentials.
          </p>
        </div>
      </section>
    </div>
  );
}
