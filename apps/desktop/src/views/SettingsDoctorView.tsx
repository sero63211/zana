import { CoreApiError } from "../api/client";
import { basename, formatBytes, formatDateTime } from "../api/format";
import type { DiagnosticCheck, HardwareProfile } from "../api/types";
import { useSystemDoctor, useSystemProfile } from "../hooks/useCoreApi";
import { Icon } from "../icons";

function describeError(error: unknown): string {
  if (error instanceof CoreApiError) {
    const actions = error.actions.length > 0 ? ` Actions: ${error.actions.join(", ")}.` : "";
    return `${error.message}${actions}`;
  }
  return "ZANA Core could not complete diagnostics. Retry, and restart the app if it persists.";
}

function aggregateTone(health: "healthy" | "pass_with_limited_features" | "failed" | undefined): "success" | "neutral" | "error" {
  switch (health) {
    case "healthy":
      return "success";
    case "pass_with_limited_features":
      return "neutral";
    case "failed":
      return "error";
    default:
      return "neutral";
  }
}

function aggregateLabel(health: "healthy" | "pass_with_limited_features" | "failed" | undefined): string {
  switch (health) {
    case "healthy":
      return "Healthy";
    case "pass_with_limited_features":
      return "Passing with limited features";
    case "failed":
      return "Needs attention";
    default:
      return "No report available";
  }
}

function EvidenceRow({ check }: { check: DiagnosticCheck }) {
  const evidence = check.evidence;
  const notes = evidence.notes.length > 0 ? evidence.notes.join(" · ") : null;
  return (
    <dl className="fact-grid fact-grid--compact">
      {fact("Observed source", evidence.observed_source)}
      {fact("Observed value", evidence.value === null ? null : String(evidence.value))}
      {fact("Basename", evidence.basename)}
      {fact("Digest prefix", evidence.digest_prefix)}
      {fact("Boolean presence", evidence.boolean_presence === null ? null : evidence.boolean_presence ? "Present" : "Absent")}
      {fact("Notes", notes)}
    </dl>
  );
}

function fact(label: string, value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function CheckRow({ check }: { check: DiagnosticCheck }) {
  return (
    <article className="doctor-card">
      <div className="doctor-card__head">
        <div>
          <h3>{check.name}</h3>
          <p className="doctor-card__id">{check.check_id} · severity {check.severity}</p>
        </div>
        <span className={`badge badge--check--${check.status}`}>{check.status}</span>
      </div>
      <dl className="fact-grid fact-grid--compact">
        {fact("Duration", `${check.duration_seconds.toFixed(3)} s`)}
        {fact("Source", check.observed_source)}
        {fact("Observed at", formatDateTime(check.observed_at))}
      </dl>
      <h4>Evidence</h4>
      <EvidenceRow check={check} />
      {check.issues.length > 0 ? <h4>Issues</h4> : null}
      {check.issues.map((issue) => (
        <div className="issue-row" key={`${check.check_id}:${issue.code}`}>
          <p><strong>{issue.code}</strong> ({issue.severity}) {issue.message}</p>
          {issue.recovery_actions.length > 0 ? (
            <ul className="recovery-list">
              {issue.recovery_actions.map((action) => (
                <li key={action.code}>
                  {action.code}: {action.message}{action.optional ? " (optional)" : ""}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ))}
      {check.feature_readiness.length > 0 ? <h4>Feature readiness</h4> : null}
      <div className="feature-list">
        {check.feature_readiness.map((feature) => (
          <p key={feature.feature}>
            <strong>{feature.feature}</strong>: {feature.ready ? "Ready" : "Not ready"}
            {feature.missing_reason ? ` — ${feature.missing_reason}` : ""}
          </p>
        ))}
      </div>
    </article>
  );
}

function BackendRow({ backend }: { backend: HardwareProfile["runtime_backends"][number] }) {
  return (
    <div className="backend-row">
      <p><strong>{backend.backend}</strong> · {backend.role} · {backend.installed ? "installed" : "not installed"}</p>
      <p className="backend-row__meta">
        {backend.detected_via ?? "Detection not reported"}
        {backend.error ? ` · ${backend.error}` : ""}
      </p>
    </div>
  );
}

function HardwareProfileSection({ profile }: { profile: HardwareProfile }) {
  return (
    <section className="panel-section" aria-labelledby="hardware-title">
      <div className="section-heading">
        <p className="eyebrow">Host hardware</p>
        <h2 id="hardware-title">Hardware profile</h2>
      </div>
      <dl className="fact-grid">
        {fact("OS / arch", `${profile.os} · ${profile.arch}`)}
        {fact("CPU", profile.cpu.name)}
        {fact("Logical cores", profile.cpu.logical_cores)}
        {fact("Memory", profile.memory.total_bytes === null ? null : formatBytes(profile.memory.total_bytes))}
        {fact("Memory available", profile.memory.available_bytes === null ? null : formatBytes(profile.memory.available_bytes))}
        {fact("Disk free", profile.disk.free_bytes === null ? null : formatBytes(profile.disk.free_bytes))}
        {fact("Disk location", basename(profile.disk.path))}
      </dl>
      {profile.accelerators.length > 0 ? <h3>Accelerators</h3> : null}
      <div className="backend-list">
        {profile.accelerators.map((accelerator) => (
          <div className="backend-row" key={accelerator.kind + (accelerator.name ?? "")}>
            <p><strong>{accelerator.name ?? accelerator.kind}</strong> · {accelerator.kind}</p>
            <p className="backend-row__meta">
              {accelerator.vram_total_bytes === null ? "VRAM not reported" : `${formatBytes(accelerator.vram_total_bytes)} VRAM`}
              {accelerator.detected_via ? ` · ${accelerator.detected_via}` : ""}
            </p>
          </div>
        ))}
      </div>
      {profile.runtime_backends.length > 0 ? <h3>Runtime backends</h3> : null}
      <div className="backend-list">
        {profile.runtime_backends.map((backend) => <BackendRow backend={backend} key={backend.backend} />)}
      </div>
      {profile.training_backends.length > 0 ? <h3>Training backends</h3> : null}
      <div className="backend-list">
        {profile.training_backends.map((backend) => <BackendRow backend={backend} key={backend.backend} />)}
      </div>
    </section>
  );
}

export function SettingsDoctorView() {
  const doctorQuery = useSystemDoctor();
  const profileQuery = useSystemProfile();
  const isLoading = doctorQuery.isPending || profileQuery.isPending;
  const firstError = doctorQuery.error ?? profileQuery.error ?? null;
  const doctor = doctorQuery.data;
  const profile = profileQuery.data;

  function retryAll() {
    void doctorQuery.refetch();
    void profileQuery.refetch();
  }

  return (
    <div className="view-stack">
      <section className="section-block" aria-labelledby="settings-title">
        <div className="section-heading">
          <p className="eyebrow">Configuration</p>
          <h2 id="settings-title">Settings and diagnostics</h2>
          <p className="section-intro">
            Diagnostics are read-only, loopback-authenticated, and report only redacted evidence. Recovery actions
            are instructions; ZANA never runs them automatically.
          </p>
        </div>

        {isLoading ? (
          <div className="loading-panel" role="status" aria-live="polite">
            <span className="loading-panel__mark" aria-hidden="true"><Icon name="refresh" size={16} /></span>
            <div>
              <p className="eyebrow">Loading</p>
              <p>Running the bounded doctor probes and reading the hardware profile.</p>
            </div>
          </div>
        ) : firstError ? (
          <div className="status-panel status-panel--error" role="alert">
            <span className="status-mark" aria-hidden="true"><Icon name="alert" size={18} /></span>
            <div className="status-panel__body">
              <p className="eyebrow">Diagnostics unavailable</p>
              <h2>Could not load the doctor report</h2>
              <div className="status-panel__copy">
                <p>{describeError(firstError)}</p>
              </div>
              <div className="status-panel__actions">
                <button type="button" className="primary-action" onClick={retryAll}>
                  <Icon name="refresh" size={16} />
                  Retry diagnostics
                </button>
              </div>
            </div>
          </div>
        ) : doctor ? (
          <>
            <div className={`doctor-summary doctor-summary--${aggregateTone(doctor.aggregate_health)}`} role="status">
              <p className="eyebrow">Aggregate health</p>
              <h3>{aggregateLabel(doctor.aggregate_health)}</h3>
              <p>
                {doctor.checks.length} checks · {doctor.error_count} errors · {doctor.skipped_or_unavailable_count} skipped or unavailable
                · generated {formatDateTime(doctor.generated_at)}
              </p>
            </div>
            <div className="doctor-list">
              {doctor.checks.map((check) => <CheckRow key={check.check_id} check={check} />)}
            </div>
          </>
        ) : null}
      </section>

      {profile ? <HardwareProfileSection profile={profile} /> : null}

      <section className="safety-panel safety-panel--quiet" aria-labelledby="token-safety-title">
        <span className="safety-panel__icon" aria-hidden="true"><Icon name="lock" size={18} /></span>
        <div>
          <p className="eyebrow">Credential safety</p>
          <h2 id="token-safety-title">The Core token stays in memory</h2>
          <p>
            The per-launch loopback token is held only by the desktop process and never rendered, persisted, or
            logged. Diagnostics report status and redacted evidence, never credentials.
          </p>
        </div>
      </section>
    </div>
  );
}
