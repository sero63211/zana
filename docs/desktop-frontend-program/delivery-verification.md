# Delivery, Verification, Recovery, and Acceptance Program

## 1. Execution authority

This document does not start implementation.

Implementation begins only after a current explicit Founder `/goal` command referencing this package. At that point:

- Kimi K3 at maximum reasoning is the Design Director, sole UX authority, desktop frontend architect/code owner, and final visual acceptance owner.
- Kimi makes 100% of design, navigation, interaction, window behavior, component, UI-state, responsive, and frontend decisions.
- Kimi may orchestrate multiple visible first-class `router_opencode_go_deepseek_v4_flash` agents using model `opencode-go/deepseek-v4-flash` at reasoning effort `max` only after that Founder command.
- Those workers may own mutually disjoint backend/API/database/runtime/security/job-event/packaging/shared-contract/non-visual-test lanes. They may not design the interface, change user-visible behavior, or invent UX.
- DeepSeek V4 Pro and silent model substitution are forbidden. There is no separate DeepSeek Max model; `max` is the required reasoning effort for Flash.
- Kimi specifies each worker's exact user-visible expectation, input/output contract, allowed files, dependency, verification, stop conditions, and interface escalation rule; monitors it; reviews it; and proves integration.
- Workers do not stage, commit, push, deploy, run/download local models, or access production/provider state. The ZANA lead/root performs accepted integration under repository rules.
- One writer per worktree and file ownership domain. Shared schemas/contracts have one owner/integrator until stable.

## 2. Preconditions

Before the first writer:

1. Re-read `AGENTS.md`, GoalBuddy goal/state, adoption map, ledger, accepted handoffs, full controlling spec graph, this package, and current primary sources.
2. Verify canonical root, HEAD, branch, remote, clean status, worktrees, task ownership, and current host-safety policy.
3. Rebuild the current-vs-proposed ledger from current registered code. Active branches are not truth.
4. Freeze or assign a single owner for API schemas, event framing, error envelopes, operation identity, native path approval, and frontend wire types.
5. Prove that no worker ownership overlaps and no proposed interface is being consumed as implemented.
6. Record which verification classes are authorized now. Live runtime/model/native/provider work remains prohibited unless the current Founder command explicitly allows it.

Stop before any frontend implementation if Core/frontend versions, job semantics, path approval, or a required P0 contract remain ambiguous enough to change architecture.

## 3. Dependency-ordered rollout

### Phase 0 — Evidence re-baseline and contract freeze

Deliver:

- exact canonical API/DB/event/native inventory;
- resolved stale Models pull copy;
- one stable frontend/Core version handshake;
- committed/generated or single-owned runtime-validated wire contract;
- accepted operation/cursor/error/freshness/path/idempotency conventions;
- Kimi's signed-off final IA, screen inventory, interaction principles, and design tokens.

Gate: no screen consumes a route that lacks an accepted contract test. No visual implementation starts before Kimi signs the state inventory.

### Phase 1 — Secure desktop foundation

Kimi-owned frontend: routing, grouped navigation, Core session boundary, query/error/state primitives, modal/focus system, responsive window behavior, density/tokens, global job indicator shell.

Potential Flash lanes, disjoint and nonvisual: Tauri command allowlist/capabilities; native dialog/approved path; OS credential references; schema generation; version handshake tests.

Gate: token never persists/renders; CSP and capabilities are least privilege; window works at required sizes; keyboard/modal primitives pass; Core failure/restart states are real.

### Phase 2 — Discover and diagnose

Kimi implements Dashboard, Models/Runtimes/Acquisition, System Doctor, and read-only resource facts from accepted contracts.

Gate: real empty/populated/offline/invalid/discovery/pull/cancel/recovery states; no fake counts; acquisition success requires discovery confirmation; all current stale copy removed.

### Phase 3 — Durable Jobs infrastructure

Kimi implements authenticated bounded fetch-SSE pages, cursor persistence for the session, snapshot reconciliation, global Jobs list/detail, and contextual job components.

Potential Flash lane: job list/filter/target/cancellability/operation-id contracts and tests.

Gate: refresh/reconnect never loses or duplicates applied event ids; transport abort never cancels Core; terminal status comes from snapshot; byte/event/history caps verified.

### Phase 4 — Capability and knowledge authoring

Kimi implements Capability catalog/editor, native source intake, validation/provenance, unsaved/stale/conflict behavior, Knowledge source/snapshot/retrieval inspectors.

Potential disjoint Flash lanes: missing source types/removal/concurrency; knowledge pipeline/job/API/persistence; provider availability projections.

Gate: create/save/reopen/validate real data; unsupported/symlink/size/divergence failures; original files untouched; no full host path rendered; offline snapshot inspection proven.

### Phase 5 — Builds and evaluations

Kimi implements exact model/capability/policy selection, analysis, baseline, immutable plan review, approval, timeline/cancel/recovery, evaluation comparison/gates/failures.

Potential Flash lanes: build execution API/DB/jobs; evaluation persistence/report APIs; resource/approval contracts. Shared build/evaluation schema remains one owner.

Gate: actual phases only; plan changes invalidate approval; failed gate cannot yield verified image; partial/cancelled artifacts never promoted; exact real report evidence.

### Phase 6 — Images and portability

Kimi implements registry/detail/runnability/integrity and native Export/Import workflows.

Potential Flash lanes: image detail/reference/verify/delete API; portability product API/jobs/atomic registration; packaging/security negative tests.

Gate: OCI digest-preserving round trip; corrupted/traversal/secret archive rejected; missing Base Model imports as not runnable; no secrets/Instance state; delete reference guard.

### Phase 7 — Instances, chat, provenance, memory

Kimi implements Instance preflight/lifecycle, bounded chat stream, source/tool/image provenance, memory proposals/reset, image switch/rollback.

Potential Flash lanes: persistent instance/chat/memory API; streaming framing; runtime tool schema continuation; DB integration.

Gate: exact runtime/model/image binding; real local answer; provenance from stored record; tool permission enforced in Core; cancel/timeout/partial answer honest; reset scopes and rollback proven.

### Phase 8 — Observability, Resources, Settings

Kimi implements local redacted operation views, sink/retention health, resource policies/leases/admission explanation, and typed persisted Settings.

Potential Flash lanes: event emission/API, resource API/settings persistence, diagnostic export, data-root relocation, signed updater packaging where separately authorized.

Gate: telemetry remains off; no raw/private values; unknown headroom blocks heavy work; settings rollback/restart semantics proven; no fabricated time series.

### Phase 9 — Integration, native release, and acceptance

Lead integrates accepted scopes serially. Kimi runs final UX/visual/accessibility acceptance and end-to-end contract review. Broader/live verification occurs only under current authority.

Gate: every mandatory acceptance item maps to exact command, artifact, screenshot/state proof, and accepted SHA; final Judge reports full outcome complete.

## 4. Verification stack

Run the smallest relevant check first, then broaden only when the touched surface/risk justifies it and current host policy permits.

### Static and unit gates

- `git diff --check`; allowlist/ownership review; secret/path/token string scan.
- TypeScript strict typecheck; ESLint/format; Rust fmt/clippy/tests; Python Ruff/Pyright/focused pytest for touched backend lanes.
- Runtime validators for every response/error/event, including malformed/missing required arrays, wrong enums, nonfinite numbers, oversized strings, hostile metadata, and unknown fields.
- Component tests for keyboard, focus, semantics, status announcements, form preservation, confirmation, error mapping, and disabled reason.
- Reducer/state-machine tests that prove every transition and reject invalid success/promotion.
- API contract tests for auth, pagination, freshness, idempotency, optimistic concurrency, cancellation, and redaction.

### Streaming and job gates

- Fragmented UTF-8/SSE lines, multi-event chunks, comments, empty pages, malformed ids, cursor ahead/behind, duplicate ids, gap detection, oversized event/page, disconnect between submission and response.
- Snapshot/event disagreement resolves to canonical snapshot and raises visible stale/contract state.
- Fetch abort releases reader; reconnect uses last accepted id; no token in URL; bounded buffers/history; no token-by-token live-region noise.
- Cancel request timing before start, during work, after terminal, during disconnect, and after app relaunch.
- Interrupted Core/app recovery proves durable status and partial-artifact policy.

### Accessibility gates

- Automated axe-equivalent checks in component/web E2E plus manual VoiceOver. Automation is not sufficient alone.
- Keyboard-only traversal for navigation, tables, tabs, forms, drawers, dialogs, timelines, logs, chat, and source detail.
- Modal focus containment/return; least-destructive initial focus; Escape policy; no focus theft on status updates.
- WCAG AA contrast, forced colors, visible focus, non-color status, 200% text, 400% zoom/320 px reflow, reduced motion.
- Screen-reader announcements for phase/status/completion/error; exact labels for icons and progress.

### Visual gates

- Kimi-approved deterministic screenshots for all screen/state combinations in `product-blueprint.md`.
- Production build has no fixture switch, fake-data service, placeholder state, or screenshot-only CSS branch.
- Test-only fixtures are schema-valid, unmistakably nonproduction, injected at the API boundary, and cannot be selected in a release build.
- Long names/digests, unknown values, empty arrays, large counts, locale expansion, and hostile line breaks do not destroy hierarchy.

### Security and privacy gates

- Missing/wrong bearer rejected; token absent from DOM, URL, storage, logs, errors, screenshots, clipboard, exports, and diagnostic bundle.
- CSP has no remote scripts or broad connect wildcard; capabilities/commands are window-scoped; no arbitrary shell/frontend filesystem access.
- Native picker paths are revalidated by Core; traversal/symlink/corruption/secret import negative tests; export excludes secrets/mutable state.
- Untrusted document/model/log/runtime content renders as text; no HTML execution; raw error/detail discarded.
- Permission changes invalidate approval; denied tool never executes; imported unknown adapter remains untrusted/not runnable as required.

### Resource and performance gates

- Unknown/insufficient memory/disk, lease conflicts, category/worker/item limits, and pressure recovery are rendered from Core decisions.
- No auto-relaxation, auto-delete, auto-download, or hidden background polling.
- Measure every budget in `product-blueprint.md`; preserve traces/results with host/app/Core versions.
- Stress bounded UI using synthetic test-only contract data (10k catalog rows, 1k events, fragmented streams) without running models/providers.

### Integration/live gates when explicitly authorized

- Real Ollama off/on/no models/model present/manual OpenAI-compatible/invalid known-port cases.
- Real model pull with disk admission, progress, cancel, post-pull discovery.
- Capability create/add behavior/document/evals/save/reopen; real local knowledge parse/embed/index/retrieval offline.
- Real baseline/candidate/gate; failed gate no promotion; immutable image inspect.
- Instance real local answer, RAG provenance, calculator permission, memory reset.
- OCI export/delete-registration/import/same digest/missing model/corrupt archive/offline rerun.
- Real compatible MLX adapter pipeline may pass or fail honestly; never fake improvement.
- macOS ARM64 package builds, launches, starts Core, reports failure, and passes accessibility/first-run recovery.

## 5. Failure recovery requirements

| Failure | Required safe state | Allowed recovery |
| --- | --- | --- |
| Core launch/exit | Shell stays usable; token cleared; reason sanitized. | One explicit restart; app restart if replacement blocked. |
| Auth/version mismatch | No API data trusted or mutations retried. | Restart/upgrade exact component; show versions. |
| Read failure | Last confirmed data retained and labeled stale if available. | Abort/retry read; navigate to Doctor/logs. |
| Mutation uncertain | Do not claim fail or success; preserve input/operation id. | Reconcile exact target/job, then continue or create new operation. |
| Stream disconnect | Job continues; cursor/history retained. | Snapshot reconcile and bounded reconnect. |
| Build/runtime drift | Plan/approval invalid; build blocked; old images safe. | Refresh, re-analyze as new plan/job. |
| Cancel during long work | Partial result unusable; logs/checkpoints retained per Core; prior images safe. | Resume only from declared checkpoint or new job. |
| Disk/resource denial | No partial heavy operation begins. | Free resources/change bounded policy/select lighter planner-approved path, then new admission. |
| DB busy/migration | No destructive reset; UI blocks dependent writes. | Doctor-guided close other writer/restart/backup/migration repair. |
| Import corruption/traversal | Nothing registered; temporary workspace cleanup reported. | Select trusted archive/re-export; never weaken validation. |
| Export write/replace failure | Existing destination preserved; partial cleaned or uncertainty explicit. | New destination or exact safe retry. |
| Instance start/stop uncertainty | Prevent duplicate session/replacement; active binding not guessed. | Reconcile runtime session; manual restart path if Core instructs. |
| Chat timeout/cancel | Partial response labeled; provenance of completed steps retained; no duplicate send. | Continue with new message or explicit retry after conversation refresh. |
| Settings save/relocation | Prior settings/data root remains authoritative or uncertainty blocks work. | Restore prior snapshot/complete recovery under Core instruction. |

## 6. Kill switches

Kill switches are local, explicit, auditable Core policy. They are not remote telemetry flags and the frontend cannot override them.

Required independently testable switches:

- disable all model acquisition/network actions;
- force offline/default-deny tool network;
- disable adapter training while keeping nontraining plans available;
- disable tool execution while allowing chat/RAG where policy permits;
- disable new build starts while preserving inspect/cancel/recovery;
- disable import and/or export separately;
- disable instance starts while preserving stop/inspect;
- disable diagnostic bundle export;
- disable updater checks/install independently, if updater exists;
- safe mode: Core health, Doctor, Jobs inspect/cancel, logs, and immutable inspection only.

Each screen shows the switch source (operator policy, safe mode, compatibility, build config), affected action, and recovery route. A switch never hides existing data or fabricates unsupported status.

## 7. Observability requirements

For every consequential operation, record a bounded local structured event with:

- timestamp, schema version, severity, operation/job id, phase, progress when real;
- related nonsecret capability/build/image/instance ids or digest prefixes;
- outcome/recovery code and duration when available;
- resource admission/lease identity where relevant;
- redacted bounded payload; no document body, prompt/response by default, host path, token, credential, environment, or raw exception.

The frontend reports its own local presentation failures only through a bounded native/Core bridge after that contract exists. It must not create remote analytics. Event/log retention gaps, sink failure, and rotation uncertainty are visible.

## 8. Rollout and rollback

- Ship behind local capability flags by vertical, not a single "new UI" flag: jobs client, capability editor, build/evaluation, images/portability, instances/chat, observability/resources/settings.
- Default a vertical off until its P0 contracts and tests are accepted. Disabled verticals show honest unavailable state or keep the prior working surface.
- Use additive route/component integration; never leave two components mutating the same entity through divergent contracts.
- Database/API changes land before the consuming frontend; backward compatibility spans at least the coordinated desktop/Core release pair.
- Rollback must preserve user DB/artifacts and accepted jobs. Never downgrade schema or delete data automatically. If a frontend rollback cannot understand newer Core state, fail with version mismatch and a safe upgrade/recovery route.
- Tauri updater, if later enabled, uses signed artifacts and an explicit operator action; no hidden mandatory network check in local/offline mode.

## 9. Exact completion criteria

The desktop frontend program is complete only when all are true:

1. Every destination and state in this package is implemented or explicitly removed by a new Founder/spec decision; no placeholder/dead production branch remains.
2. Every P0/P1 contract gap used by a screen is closed with accepted contract tests and current documentation.
3. No real-state claim comes from fixtures, derived guesses, stale copy, raw metadata, or frontend persistence.
4. All focused static/unit/contract/streaming/a11y/visual/security/resource/performance gates pass on accepted SHAs.
5. Kimi records final visual and UX acceptance across the required state/viewport/accessibility matrix.
6. Lead integration produces clean index/worktree proof, focused accepted commits, and confirmed non-force remote SHAs under repository policy.
7. Native macOS ARM64 build/launch/Core lifecycle evidence and real local end-to-end evidence are complete under explicit authority.
8. Every mandatory item in controlling `25_ACCEPTANCE_CRITERIA.md` has fresh exact evidence; training is real and may honestly fail its quality gate.
9. Final security review finds no token/secret/path/content leakage, broad Tauri authority, unsafe archive/path behavior, or permission bypass.
10. Final Judge reports `full_outcome_complete: true`. Kimi/worker completion, a green screenshot, a passing mocked E2E, or a completed plan alone is never enough.
