import { useState, type FormEvent, type ReactNode } from "react";

import { CoreApiError } from "../api/client";
import {
  formatBytes,
  formatCount,
  formatDateTime,
  identityStrengthLabel,
  jobStatusLabel,
  modelDisplayName,
  runtimeKindLabel,
  runtimeMetadata,
  runtimeStatus,
} from "../api/format";
import type { JobRead, ModelPullPayload, ModelRead, RuntimeKind, RuntimeRead } from "../api/types";
import {
  useAddRuntime,
  useDeleteRuntime,
  useModels,
  usePullModel,
  useRefreshRuntimes,
  useRuntimes,
} from "../hooks/useCoreApi";
import { Icon } from "../icons";

function describeError(error: unknown): string {
  if (error instanceof CoreApiError) {
    const actions = error.actions.length > 0 ? ` Actions: ${error.actions.join(", ")}.` : "";
    return `${error.message}${actions}`;
  }
  return "ZANA Core could not complete this request. Retry, and restart the app if it persists.";
}

function jobActions(job: JobRead): string[] {
  const actions = job.error_json?.actions;
  return Array.isArray(actions) ? actions.filter((item): item is string => typeof item === "string") : [];
}

function JobNotice({ job }: { job: JobRead | null }) {
  if (!job) return null;
  const tone = job.status === "SUCCEEDED" ? "success" : job.status === "FAILED" ? "error" : "info";
  const code = typeof job.error_json?.code === "string" ? job.error_json.code : null;
  const actions = jobActions(job);
  const isPull = job.kind === "model_pull";
  return (
    <div className={`notice notice--${tone}`} role="status" aria-live="polite">
      <p className="eyebrow">{isPull ? "Model pull job" : "Discovery job"}</p>
      <h3>Job #{job.id} {jobStatusLabel(job.status)}</h3>
      <p>{job.message}</p>
      {code ? <p className="notice__code">Status code: {code}</p> : null}
      {actions.length > 0 ? <p className="notice__actions">Recovery: {actions.join(", ")}</p> : null}
      {isPull ? (
        <p>
          The Core persisted an approved native acquisition plan for this runtime. It does not execute the pull
          yet, so no bytes were downloaded and no model was installed.
        </p>
      ) : null}
    </div>
  );
}

function LoadingPanel() {
  return (
    <div className="loading-panel" role="status" aria-live="polite">
      <span className="loading-panel__mark" aria-hidden="true"><Icon name="refresh" size={16} /></span>
      <div>
        <p className="eyebrow">Loading</p>
        <p>Reading the live runtime and model catalog from ZANA Core.</p>
      </div>
    </div>
  );
}

function QueryError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div className="status-panel status-panel--error" role="alert">
      <span className="status-mark" aria-hidden="true"><Icon name="alert" size={18} /></span>
      <div className="status-panel__body">
        <p className="eyebrow">Catalog unavailable</p>
        <h2>Could not load runtimes or models</h2>
        <div className="status-panel__copy">
          <p>{describeError(error)}</p>
        </div>
        <div className="status-panel__actions">
          <button type="button" className="primary-action" onClick={onRetry}>
            <Icon name="refresh" size={16} />
            Retry
          </button>
        </div>
      </div>
    </div>
  );
}

function EmptyCatalog() {
  return (
    <div className="state-panel state-panel--neutral" role="status">
      <p className="eyebrow">Empty catalog</p>
      <h3>No runtimes or models yet</h3>
      <p>
        Start a supported local runtime (for example Ollama), add a manual endpoint below, then run discovery.
        ZANA lists models only after a real probe returns them.
      </p>
    </div>
  );
}

function RuntimeRow({ runtime }: { runtime: RuntimeRead }) {
  const meta = runtimeMetadata(runtime);
  const status = runtimeStatus(runtime);
  const deleteMutation = useDeleteRuntime();
  const [confirming, setConfirming] = useState(false);
  const isManual = runtime.source === "manual";

  function handleDelete() {
    if (!isManual) return;
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    deleteMutation.mutate({ runtimeId: runtime.id });
  }

  const facts: Array<[string, string | null | undefined]> = [
    ["Endpoint", runtime.endpoint],
    ["Last seen", formatDateTime(runtime.last_seen_at)],
    ["Server running", meta.server_running === undefined ? null : meta.server_running ? "Yes" : "No"],
    ["Installed", meta.installed === undefined ? null : meta.installed ? "Yes" : "No"],
    ["Registered", meta.registered === undefined ? null : meta.registered ? "Yes" : "No"],
    ["Evidence", meta.evidence ? (meta.evidence.length > 0 ? meta.evidence.join(", ") : null) : null],
    ["Warnings", meta.warnings ? (meta.warnings.length > 0 ? meta.warnings.join(", ") : null) : null],
    ["Probe error", meta.error],
  ];

  return (
    <article className="runtime-row">
      <div className="runtime-row__head">
        <div>
          <h3>{runtimeKindLabel(runtime.kind)} <span className="runtime-row__id">#{runtime.id}</span></h3>
          <p className="runtime-row__endpoint">{runtime.endpoint}</p>
        </div>
        <div className="runtime-row__badges">
          <span className={`badge badge--${status.tone}`}>{status.label}</span>
          <span className="badge badge--source">{runtime.source}</span>
          {isManual && !deleteMutation.isPending ? (
            <button
              type="button"
              className="icon-button icon-button--danger"
              aria-label={confirming ? `Confirm removal of manual runtime ${runtime.id}` : `Remove manual runtime ${runtime.id}`}
              onClick={handleDelete}
            >
              <Icon name={confirming ? "alert" : "shield"} size={14} />
              {confirming ? "Confirm delete" : "Delete"}
            </button>
          ) : null}
          {deleteMutation.isPending ? <span className="badge badge--idle">Removing…</span> : null}
        </div>
      </div>
      <dl className="fact-grid">
        {facts.filter(([, value]) => value !== null && value !== "").map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {deleteMutation.isError ? <p className="field-error" role="alert">{describeError(deleteMutation.error)}</p> : null}
    </article>
  );
}

function fact(label: string, value: string | number | null | undefined): ReactNode {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function ModelRow({ model }: { model: ModelRead }) {
  const meta = model.metadata_json;
  const displayName = modelDisplayName(model);
  const parameterLabel = typeof meta.parameter_label === "string" ? meta.parameter_label : null;
  const trainability = typeof meta.trainability === "string" ? meta.trainability : null;
  const metadataSource = typeof meta.metadata_source === "string" ? meta.metadata_source : null;
  const capabilities = model.capabilities_json.length > 0 ? model.capabilities_json.join(", ") : null;

  return (
    <article className="model-row">
      <div className="model-row__head">
        <div>
          <h3>{displayName}</h3>
          <p className="model-row__meta">{model.model_id} · runtime #{model.runtime_id}</p>
        </div>
        <span className={`badge badge--identity badge--${model.identity_strength}`}>
          {identityStrengthLabel(model.identity_strength)}
        </span>
      </div>
      <dl className="fact-grid">
        {fact("Family", model.family)}
        {fact("Size", model.size_bytes === null ? null : formatBytes(model.size_bytes))}
        {fact("Quantization", model.quantization)}
        {fact("Context", model.context_length === null ? null : `${formatCount(model.context_length)} tokens`)}
        {fact("Format", model.format)}
        {fact("Parameters", parameterLabel ?? (model.parameter_count === null ? null : formatCount(model.parameter_count)))}
        {fact("Capabilities", capabilities)}
        {fact("Last seen", formatDateTime(model.last_seen_at))}
      </dl>
      <details className="model-details">
        <summary>Full returned metadata</summary>
        <dl className="fact-grid">
          {fact("Key", model.key)}
          {fact("Digest", model.digest)}
          {fact("Parameter count", model.parameter_count === null ? null : formatCount(model.parameter_count))}
          {fact("Parameter label", parameterLabel)}
          {fact("Trainability", trainability)}
          {fact("Metadata source", metadataSource)}
        </dl>
      </details>
    </article>
  );
}

function validateEndpoint(value: string): string | null {
  const trimmed = value.trim();
  try {
    const url = new URL(trimmed);
    if (url.protocol !== "http:" && url.protocol !== "https:") return "Use an absolute http(s) URL.";
    if (!url.hostname) return "Use an absolute http(s) URL.";
    if (url.username || url.password) return "Do not embed credentials in the endpoint.";
    return null;
  } catch {
    return "Use an absolute http(s) URL.";
  }
}

function ManualRuntimePanel() {
  const addMutation = useAddRuntime();
  const [kind, setKind] = useState<RuntimeKind>("ollama");
  const [endpoint, setEndpoint] = useState("");
  const [endpointError, setEndpointError] = useState<string | null>(null);
  const [added, setAdded] = useState(false);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const error = validateEndpoint(endpoint);
    setEndpointError(error);
    if (error) return;
    setAdded(false);
    addMutation.mutate(
      { payload: { kind, endpoint: endpoint.trim() } },
      {
        onSuccess: () => {
          setEndpoint("");
          setAdded(true);
        },
      },
    );
  }

  return (
    <section className="panel-section" aria-labelledby="manual-runtime-title">
      <div className="section-heading">
        <p className="eyebrow">Manual endpoint</p>
        <h2 id="manual-runtime-title">Add a runtime manually</h2>
      </div>
      <form className="form-stack" onSubmit={handleSubmit}>
        <div className="form-grid-two">
          <div className="form-field">
            <label htmlFor="manual-runtime-kind">Adapter / runtime kind</label>
            <select id="manual-runtime-kind" value={kind} onChange={(event) => setKind(event.target.value as RuntimeKind)}>
              <option value="ollama">Ollama</option>
              <option value="lm-studio">LM Studio</option>
              <option value="llama.cpp">llama.cpp</option>
              <option value="mlx-lm">MLX LM</option>
              <option value="openai-compatible">OpenAI-compatible</option>
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="manual-runtime-endpoint">Endpoint URL</label>
            <input
              id="manual-runtime-endpoint"
              type="url"
              value={endpoint}
              onChange={(event) => setEndpoint(event.target.value)}
              placeholder="http://127.0.0.1:1234"
              aria-describedby={endpointError ? "manual-runtime-endpoint-error" : undefined}
            />
            {endpointError ? <p className="field-error" id="manual-runtime-endpoint-error" role="alert">{endpointError}</p> : null}
          </div>
        </div>
        {addMutation.isError ? <p className="field-error" role="alert">{describeError(addMutation.error)}</p> : null}
        {added ? <p className="notice notice--success" role="status">Runtime added. Run discovery to probe it.</p> : null}
        <div className="action-strip">
          <button type="submit" className="primary-action" disabled={addMutation.isPending}>
            <Icon name="plus" size={16} />
            {addMutation.isPending ? "Adding…" : "Add manual runtime"}
          </button>
        </div>
      </form>
    </section>
  );
}

function validateModelReference(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return "Enter a model reference.";
  if (trimmed.length > 200) return "Model reference must be 200 characters or fewer.";
  if (trimmed === "." || trimmed === "..") return "Model reference cannot be . or ..";
  if (hasControlCharacters(trimmed)) return "Model reference contains unsupported control characters.";
  return null;
}

function hasControlCharacters(value: string): boolean {
  for (const char of value) {
    const code = char.charCodeAt(0);
    if (code < 32 || code === 127) return true;
  }
  return false;
}

function validateOptionalNumber(value: string, max: number, label: string): string | null {
  if (value === "") return null;
  if (!/^\d+$/.test(value)) return `${label} must be a whole number.`;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed > max) return `${label} must be between 0 and ${max}.`;
  return null;
}

function PullModelPanel({ runtimes }: { runtimes: RuntimeRead[] }) {
  const pullMutation = usePullModel();
  const ollamaRuntimes = runtimes.filter((runtime) => runtime.kind === "ollama");
  const [runtimeIdValue, setRuntimeIdValue] = useState("");
  const selectedRuntime = ollamaRuntimes.find((runtime) => runtime.id === Number(runtimeIdValue)) ?? ollamaRuntimes[0];
  const [modelReference, setModelReference] = useState("");
  const [expectedSize, setExpectedSize] = useState("");
  const [deadline, setDeadline] = useState("");
  const [approved, setApproved] = useState(false);
  const [touched, setTouched] = useState(false);

  const referenceError = touched ? validateModelReference(modelReference) : null;
  const sizeError = touched ? validateOptionalNumber(expectedSize, 2 ** 40, "Expected size") : null;
  const deadlineError = touched ? validateOptionalNumber(deadline, 3600, "Deadline") : null;
  const canSubmit = Boolean(
    selectedRuntime &&
    !referenceError &&
    !sizeError &&
    !deadlineError &&
    modelReference.trim() !== "" &&
    approved,
  );

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTouched(true);
    if (!selectedRuntime || !canSubmit) return;
    const payload: ModelPullPayload = {
      runtime_id: selectedRuntime.id,
      model_reference: modelReference.trim(),
      user_approved: true,
      expected_size_bytes: expectedSize === "" ? undefined : Number(expectedSize),
      deadline_seconds: deadline === "" ? undefined : Number(deadline),
    };
    pullMutation.mutate({ payload });
  }

  if (ollamaRuntimes.length === 0) {
    return (
      <section className="panel-section" aria-labelledby="pull-title">
        <div className="section-heading">
          <p className="eyebrow">Native acquisition</p>
          <h2 id="pull-title">Pull a model</h2>
        </div>
        <div className="state-panel state-panel--neutral">
          <p>
            Pulling requires an Ollama runtime record. Add or start Ollama, then run discovery; the form will
            appear when Core reports an Ollama runtime.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="panel-section" aria-labelledby="pull-title">
      <div className="section-heading">
        <p className="eyebrow">Native acquisition</p>
        <h2 id="pull-title">Pull a model through Ollama</h2>
        <p className="section-intro">
          ZANA records an approved native plan with Core. The current backend queues it and does not execute
          downloads, so this form never claims bytes were transferred.
        </p>
      </div>
      <form className="form-stack" onSubmit={handleSubmit}>
        <div className="form-field">
          <label htmlFor="pull-runtime">Ollama runtime</label>
          <select
            id="pull-runtime"
            value={selectedRuntime?.id ?? ""}
            onChange={(event) => setRuntimeIdValue(event.target.value)}
          >
            {ollamaRuntimes.map((runtime) => (
              <option key={runtime.id} value={runtime.id}>
                {runtime.endpoint} ({runtime.id})
              </option>
            ))}
          </select>
        </div>
        <div className="form-field">
          <label htmlFor="pull-reference">Model reference</label>
          <input
            id="pull-reference"
            type="text"
            maxLength={200}
            value={modelReference}
            onChange={(event) => setModelReference(event.target.value)}
            placeholder="qwen2.5:0.5b"
            aria-describedby={referenceError ? "pull-reference-error" : undefined}
          />
          {referenceError ? <p className="field-error" id="pull-reference-error" role="alert">{referenceError}</p> : null}
        </div>
        <div className="form-grid-two">
          <div className="form-field">
            <label htmlFor="pull-size">Expected size (bytes, optional)</label>
            <input
              id="pull-size"
              type="number"
              min={0}
              max={2 ** 40}
              value={expectedSize}
              onChange={(event) => setExpectedSize(event.target.value)}
              placeholder="Optional"
              aria-describedby={sizeError ? "pull-size-error" : undefined}
            />
            {sizeError ? <p className="field-error" id="pull-size-error" role="alert">{sizeError}</p> : null}
          </div>
          <div className="form-field">
            <label htmlFor="pull-deadline">Deadline (seconds, optional)</label>
            <input
              id="pull-deadline"
              type="number"
              min={1}
              max={3600}
              value={deadline}
              onChange={(event) => setDeadline(event.target.value)}
              placeholder="Optional, up to 3600"
              aria-describedby={deadlineError ? "pull-deadline-error" : undefined}
            />
            {deadlineError ? <p className="field-error" id="pull-deadline-error" role="alert">{deadlineError}</p> : null}
          </div>
        </div>
        <label className="check-row">
          <input
            type="checkbox"
            checked={approved}
            onChange={(event) => setApproved(event.target.checked)}
          />
          <span>I approve queueing a native pull to this local runtime.</span>
        </label>
        {pullMutation.isError ? <p className="field-error" role="alert">{describeError(pullMutation.error)}</p> : null}
        <div className="action-strip">
          <button type="submit" className="primary-action" disabled={!canSubmit || pullMutation.isPending}>
            <Icon name="plus" size={16} />
            {pullMutation.isPending ? "Queuing…" : "Queue approved pull"}
          </button>
          <span className="action-note">
            {approved ? "Approval is recorded with the job." : "Confirm the approval control to enable queueing."}
          </span>
        </div>
      </form>
      <JobNotice job={pullMutation.data ?? null} />
    </section>
  );
}

export function RuntimesModelsView() {
  const runtimesQuery = useRuntimes();
  const modelsQuery = useModels();
  const refreshMutation = useRefreshRuntimes();

  const isLoading = runtimesQuery.isPending || modelsQuery.isPending;
  const catalogError = runtimesQuery.error ?? modelsQuery.error ?? null;
  const runtimes = runtimesQuery.data ?? [];
  const models = modelsQuery.data ?? [];

  return (
    <div className="view-stack">
      <section className="section-block" aria-labelledby="runtimes-title">
        <div className="section-heading">
          <p className="eyebrow">Discovery</p>
          <h2 id="runtimes-title">Local runtimes and exposed models</h2>
          <p className="section-intro">
            ZANA asks runtimes on this computer which models they expose, records only what they return, and keeps
            discovered state separate from manual endpoints.
          </p>
        </div>
        <div className="action-strip">
          <button
            type="button"
            className="primary-action"
            onClick={() => refreshMutation.mutate({})}
            disabled={refreshMutation.isPending}
          >
            <Icon name="refresh" size={16} />
            {refreshMutation.isPending ? "Discovering…" : "Discover runtimes"}
          </button>
          <span className="action-note">
            Discovery probes loopback candidates only and never starts a runtime or model.
          </span>
        </div>
        <JobNotice job={refreshMutation.data ?? null} />
        {refreshMutation.isError ? <p className="field-error" role="alert">{describeError(refreshMutation.error)}</p> : null}
      </section>

      {isLoading ? (
        <LoadingPanel />
      ) : catalogError ? (
        <QueryError
          error={catalogError}
          onRetry={() => {
            void runtimesQuery.refetch();
            void modelsQuery.refetch();
          }}
        />
      ) : (
        <>
          {runtimes.length === 0 && models.length === 0 ? <EmptyCatalog /> : null}

          {runtimes.length > 0 ? (
            <section className="section-block" aria-labelledby="runtime-list-title">
              <div className="section-heading">
                <p className="eyebrow">Runtimes</p>
                <h2 id="runtime-list-title">Configured runtime records</h2>
              </div>
              <div className="runtime-list">
                {runtimes.map((runtime) => <RuntimeRow key={runtime.id} runtime={runtime} />)}
              </div>
            </section>
          ) : null}

          {models.length > 0 ? (
            <section className="section-block" aria-labelledby="model-list-title">
              <div className="section-heading">
                <p className="eyebrow">Models</p>
                <h2 id="model-list-title">Returned model descriptors</h2>
              </div>
              <div className="model-list">
                {models.map((model) => <ModelRow key={model.key} model={model} />)}
              </div>
            </section>
          ) : null}
        </>
      )}

      <ManualRuntimePanel />
      <PullModelPanel runtimes={runtimes} />
    </div>
  );
}
