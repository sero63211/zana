# ZANA

ZANA is a local-first desktop application for building and running verified,
portable AI instances from models the operator already controls. Its purpose is
not to create a new foundation model or host a model marketplace. It makes the
specialized AI lifecycle explicit: inputs, build decisions, artifacts,
evaluation evidence, permissions, and provenance can be inspected and
reproduced.

## Current implementation status

The repository has a working authenticated Core/Tauri foundation:

- a loopback-only Python Core sidecar exposes a real authenticated health
  endpoint;
- a Tauri 2 desktop shell starts the sidecar with an ephemeral per-launch
  token and stops it on clean exit;
- the React UI has a responsive seven-view navigation surface for Home,
  Runtimes & Models, Capabilities, Build & Evaluation, Images, Instances &
  Chat, and Settings & Doctor.

Only Core health is currently connected to live product data. The remaining
views deliberately show unavailable or empty states: runtime discovery,
capability editing, builds, evaluations, images, instances, chat, and
export/import are not implemented yet. ZANA does not claim synthetic records,
model results, or verification outcomes.

## Product concepts

- **Runtime** — a local or self-hosted inference service; it is not a model.
- **Base Model** — one exact model identity exposed by a Runtime or training
  backend.
- **Capability Source** — editable behavior, knowledge, examples, tools,
  permissions, and evaluation definitions; analogous to source plus build
  specification.
- **Build Plan / Build Job** — a reviewable specialization decision and one
  persisted execution of it.
- **ZANA Image** — immutable, content-addressed build output with provenance.
- **ZANA Instance** — mutable runtime state created from an Image; conversations
  and approved memories do not alter the Image.

## Target MVP workflow

1. Detect a supported local Runtime and its real models.
2. Create a Capability Source with behavior, local knowledge, optional
   examples, permissions, and evaluations.
3. Choose a Base Model and review the hardware-aware Build Plan.
4. Run the local baseline, build, and candidate evaluation jobs.
5. Promote only a passing candidate to a verified immutable ZANA Image.
6. Start a mutable ZANA Instance, chat with it, and inspect answer provenance.
7. Export an OCI-layout image; on import, validate its digests and report any
   missing Base Model rather than guessing.

The planned build strategies range from harness-only and RAG-based approaches
to compatible lightweight adapters. Training is optional; it is never assumed
to be available or successful.

## Architecture

```text
React UI (TypeScript) inside Tauri 2 desktop shell
        | typed loopback client + per-launch bearer token
        v
ZANA Core sidecar (Python / FastAPI, bound to 127.0.0.1)
        |-- SQLite metadata and persisted job/event state
        |-- immutable artifact store and knowledge snapshots
        |-- runtime adapters and model catalog
        |-- build planner, evaluation, image, and instance services
        `-- isolated worker subprocesses for training and inference work
```

React is the UI and Tauri 2 is the desktop shell. Training and inference belong
to isolated Core workers or external local runtimes, never to a UI process.
Long-running work is intended to be persisted and reported as jobs rather than
held only in frontend state.

## Privacy, security, and resource safety

- Local mode requires no account and has telemetry off by default.
- Core is loopback-only and requires an exact ephemeral bearer token; the UI
  does not render, persist, or log it.
- Network access is default-deny after explicit acquisition actions. ZANA never
  silently downloads models or artifacts.
- Tool, filesystem, and secret access are explicit permissions; arbitrary shell
  execution is not a V1 capability.
- Immutable artifacts use digests; imports must validate integrity before
  registration. Secrets and mutable Instance state do not belong in Images.
- Model acquisition and training require capacity checks and a safety reserve;
  the application must stay responsive while worker processes run.

## Repository layout

```text
apps/desktop/       React UI and Tauri 2 desktop shell
core/               Python FastAPI Core sidecar and tests
docs/product/       Durable product boundaries and naming conventions
docs/goals/         Goal and delivery coordination records
scripts/            Development, test, and sidecar-packaging helpers
.agent-work/        Operational handoffs and coordination ledger
```

## Lightweight developer workflow

Prerequisites: Python 3.12 with `uv`, Node.js with `pnpm`, and Rust/Cargo for
Tauri development.

```bash
# Install/synchronise Core dependencies
uv sync --project core

# Package the Core sidecar and launch the native desktop app
pnpm tauri:dev

# Or run only Core with a development token
ZANA_CORE_TOKEN=dev-token uv run --project core zana-core serve
```

The development Core binds to `127.0.0.1:8000`. Its health endpoint requires
the same bearer token:

```bash
curl -H "Authorization: Bearer dev-token" http://127.0.0.1:8000/api/v1/health
```

## Verification status for the UI wave

The seven-view UI handoff intentionally deferred build, lint, typecheck, test,
and Tauri-package verification; those commands were **not run for that UI
wave**. Run them after UI integration:

```bash
pnpm --dir apps/desktop lint
pnpm --dir apps/desktop typecheck
pnpm --dir apps/desktop test
pnpm --dir apps/desktop build
```

The earlier M0 foundation separately recorded passing Core and desktop checks,
including an authenticated frozen-sidecar smoke and native application launch.
That historical evidence does not verify the newer UI surface.

## Roadmap

The next implementation work settles typed domain, SQLite, job/event, and API
foundations. That unlocks real runtime discovery, capability persistence,
knowledge processing, build/evaluation, immutable images, instances, and
OCI-compatible export/import in subsequent verified slices. The end-to-end MVP
is not yet complete.
