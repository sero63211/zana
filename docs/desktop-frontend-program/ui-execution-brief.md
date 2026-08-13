# ZANA Desktop UI Execution Brief

Status: Founder-authorized implementation brief for task T927.

## Mission

Build the real ZANA desktop interface as a quiet, fast local capability workbench.
It is not a chat product, a marketing page, a generic AI dashboard, or a mockup.
The first useful thing a user sees is the real local model/runtime state and the
next available action.

The UI must stay truthful to accepted Core contracts. Missing data remains
unknown. Missing contracts become concise unavailable states. Never invent a
model, metric, job, benchmark, source, capability, compatibility result, or
download catalog.

## Product model

ZANA supports several equal use cases:

- specialize a local model with knowledge or behavior;
- compare a model before and after a build with traceable evaluations;
- package a verified result as an immutable ZANA Image;
- run a mutable local Instance from an Image;
- export or import a safe portable Image;
- target desktop or Android when the real Image/runtime contracts permit it.

Android is a target, not the identity of the whole product. Medical, Kurdish
language learning, general knowledge, and device-agent capabilities use the
same platform-neutral lifecycle. Chat is shown only inside a selected running
Instance when the real contract supports it; it is never the home screen.

## Exact information architecture

Use six stable primary destinations. Secondary destinations stay one click
away inside their section.

1. **Home** — real local models first, runtime/Core readiness, active work, and
   the next valid action.
2. **Models** — runtimes, discovered models, discovery, manual runtime setup,
   and real native model acquisition.
3. **Create** — Capabilities, Knowledge, Builds, and Evaluations.
4. **Library** — ZANA Images, Instances, and contextual Export/Import.
5. **Activity** — Jobs, Resources, Observability, and System Doctor.
6. **Settings** — local data, network, resource, privacy, and application
   settings that the Core actually exposes.

Every secondary screen needs a stable deep link. Do not add a top-level Chat,
Agents, Marketplace, or Playground destination. Do not expose a community or
official model marketplace until a real catalog contract exists. The accepted
native pull contract may be presented as **Pull model** with explicit runtime,
model identifier, disk impact, approval, durable job status, and cancellation.

## Shell and window behavior

- Desktop: a 224 px left navigation rail, compact top context bar, and one main
  list/detail workspace. Avoid nested card grids.
- Under 900 px: collapse labels to a narrow rail while preserving tooltips and
  accessible names.
- At the 720 x 560 native minimum: keep all core actions reachable without
  essential horizontal scrolling.
- At 320 CSS px / 400% zoom: reflow to a compact top bar and single-column
  content. Tables become labelled fact rows.
- Use one persistent Core status indicator. Show a job strip only when real
  active jobs exist.
- Preserve focus on route changes, restore focus after dialogs, and support
  Escape for dismissible overlays.

## Home screen

Home is model-first and compact.

1. Header row: `ZANA`, current Core state, and one contextual primary action.
2. **Local models**: a real bounded list with model name, runtime, size when
   known, exact identity strength, and readiness. Unknown fields say `Unknown`.
3. **System**: one compact runtime/Doctor/resource strip using real values.
4. **Active work**: only real non-terminal jobs; omit the section when none
   exist.
5. **Continue**: only real recently changed Capabilities, Builds, Images, or
   Instances. Omit it if the API cannot provide real records.

The zero state is exactly one sentence plus recovery actions:

`No local models found.`

Allowed actions are `Refresh` and, when supported, `Add runtime` or
`Pull model`. Do not render four empty statistic cards or explanatory essays.

## Required screen behavior

### Models

- Separate runtimes from models; never imply they are the same object.
- Default to the model list. Runtime management is a secondary tab/panel.
- Show exact runtime, digest/identity, size, family, quantization, and context
  only when reported.
- Discovery and acquisition are durable jobs. Navigation does not cancel them.
- Correct the stale queue-only copy: the accepted native Ollama pull contract
  executes an approved bounded pull and confirms discovery.
- Destructive runtime removal requires an exact target and impact confirmation.

### Create

- **Capabilities**: compact list/detail editor for real drafts and sources.
- **Knowledge**: source, snapshot, provenance, and retrieval state only from
  accepted contracts; no document body or full host path leakage.
- **Builds**: baseline, plan, approval, execution, cancellation, recovery, and
  final gate result. No animated progress without real events.
- **Evaluations**: clearly compare **Before** and **After**, show suite source,
  dataset/version, held-out status, sample count, metric definition, confidence
  or limitations, and gate decision. Official benchmark provenance must be
  visible when present; never label a custom test official.

### Library

- **Images**: immutable digest, source capability/build/evaluation evidence,
  exact model requirement, permissions, target compatibility, and integrity.
- Android/Desktop compatibility is shown as real evidence, `Unknown`, or
  `Unsupported`; never infer it from a filename or model display name.
- Export/Import are contextual Image actions with approved paths, digest
  verification, replacement policy, and honest recovery state.
- **Instances**: mutable state separated visually and semantically from the
  immutable Image. Start/stop/switch/reset actions require real contracts.
- Conversation appears only inside an Instance detail and only when available.

### Activity

- **Jobs**: real status, target, timestamps, progress/evidence, cancellability,
  retry/recovery, and terminal state. `Cancel requested` is not `Cancelled`.
- **Resources**: Core snapshots and admission decisions; no browser-derived
  guesses and no automatic policy relaxation.
- **Observability**: bounded redacted local events and sink state only.
- **Doctor**: concise checks, severity, evidence, and exact recovery actions.

### Settings

Render only persisted Core settings. Keep privacy and resource safety visible.
Never render or store a secret value. Unsupported settings are absent or marked
`Unavailable` with one reason; they are not dead controls.

## Visual system

Direction: **calm technical instrument**, not “AI aesthetic.”

- Palette: cool graphite, ink, slate, soft white, and one restrained cobalt
  accent. Success green, warning amber, and error red are semantic only.
- Forbidden: beige, orange brand accents, purple, neon, gradients, glow,
  glassmorphism, giant serif headlines, AI orbs, decorative graphs, mascots,
  and large empty hero areas.
- Prefer flat surfaces, one-pixel borders, small radii, disciplined spacing,
  and clear alignment. Shadows are rare and subtle.
- Use bundled/native typography only; no network font. Use a deliberate
  `Avenir Next`-first stack with clear numeric and monospace treatment for
  digests, versions, and job IDs.
- Use the existing lightweight inline SVG icon system. Do not add an icon or UI
  framework.
- Motion is limited to short opacity/position state transitions and respects
  `prefers-reduced-motion`.

## Copy rules

All user-visible copy is concise English.

- Page title: 1–3 words.
- Supporting line: at most 12 words, and only when it changes a decision.
- Empty state: one sentence plus one recovery action.
- Buttons start with a direct verb: `Refresh`, `Pull model`, `Create`, `Run`,
  `Cancel`, `Export`, `Import`, `Retry`.
- Avoid repeated `ZANA` and `local` labels when context is already clear.
- Never use: `AI-powered`, `unlock`, `supercharge`, `seamless`, `revolutionary`,
  `magic`, `next-level`, `welcome`, `let's`, `journey`, or `nothing here yet`.
- Errors state what failed, whether anything changed, and one recovery action.

## Truthful state model

Every data surface must handle applicable states:

- initial loading;
- empty;
- current data;
- stale or partial data;
- Core offline or incompatible;
- unsupported contract;
- validation or permission failure;
- active job and stream reconnect;
- cancel requested and cancelled;
- retryable and terminal failure;
- recovery after restart.

Use semantic status text and icons; color is never the only signal. Do not show
raw exceptions, unrestricted host paths, secrets, tokens, document bodies, or
unbounded provider output.

## Lightweight implementation rules

- Keep React 19, TypeScript, TanStack Query, Vite, and Tauri. Add no package.
- Frontend remains presentation and typed orchestration; Core owns truth.
- Keep ephemeral UI state local. Do not mirror server records into a new global
  store or localStorage.
- No background polling. Reconcile explicit refreshes, bounded streams, and
  mutation results through TanStack Query.
- Start independent reads together. Avoid sequential request waterfalls.
- Split secondary sections with direct dynamic imports when it reduces the
  initial bundle; do not create barrel imports.
- Derive simple state during render instead of synchronizing it with effects.
- Use one shared keyboard listener at most. Remove listeners on cleanup.
- Bound rendered records and apply `content-visibility: auto` to long rows.
- Avoid large dependencies, animation libraries, chart libraries, design-system
  frameworks, remote assets, and base64 artwork.
- Keep components boring, typed, and readable. Refactor oversized files before
  acceptance; do not build an abstraction for a single use.

## T927 acceptance

T927 passes only when:

1. the six-destination shell and every required secondary route are present;
2. Home is model-first and uses only real Core data;
3. all current working Models/Runtime/Doctor actions remain functional;
4. accepted current Capability/Build/Image/Instance/Job contracts are wired
   where the canonical client can validate them;
5. missing Rust cutover routes stay visibly truthful and recoverable, never
   simulated;
6. all copy follows the concise-English rules;
7. keyboard, focus, 720 x 560, and 320 CSS px reflow are covered;
8. no new dependency, fake record, placeholder metric, dead action, secret/path
   leak, background poll, or unbounded list is introduced;
9. focused UI tests, TypeScript, ESLint, formatting/diff checks, direct
   accessibility/security/error/readability review, and a focused commit pass;
10. no app, browser, native bundle, model, provider, download, training, live
    API, load, or broad E2E test is run under the current host-safety policy.

Visual launch and native acceptance remain a later explicitly authorized gate.
