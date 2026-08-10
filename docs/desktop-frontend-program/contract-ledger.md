# Current-vs-Proposed Contract Ledger

## Ledger rules

- `IMPLEMENTED` means accepted on canonical product commit `a69d7c5425b3a91aba26cb25beb30a94180faed9` and registered/reachable where an API is claimed.
- `INTERNAL_ONLY` means tested Core primitives exist but no accepted product API/persistence/application wiring exists.
- `PARTIAL` means a safe subset exists and the UI must not imply the target lifecycle is complete.
- `PROPOSED` means required by the controlling specification/frontend program but absent from canonical truth.
- Active T900 worktrees and their unaccepted changes are not contracts. Re-baseline after lead integration before implementation.
- A frontend owner may not fix a Core gap by inventing an endpoint, deriving backend state, or persisting authoritative state in React.

## Snapshot of implemented desktop truth

| Surface | Status | Evidence and limitation |
| --- | --- | --- |
| Tauri/Core lifecycle | IMPLEMENTED | One main Tauri window; Core sidecar spawn/restart/shutdown; loopback random port; fresh per-launch token; sanitized launch errors; uncertain cleanup blocks replacement. Small bind/release/spawn race remains. |
| Tauri security | PARTIAL | CSP restricts to self/loopback; only `core:default` capability; no dialog/fs/window-state/updater/credential plugins. Registered custom commands are not yet explicitly app-manifest restricted for future windows. |
| Desktop shell | IMPLEMENTED subset | Seven hash routes: Home, Runtimes & Models, Capabilities, Build & Evaluation, Images, Instances & Chat, Settings & Doctor. Current layout has 252/216 px sidebar, 720x560 native minimum, and horizontal nav below 720 CSS px. |
| Live frontend data | PARTIAL | Home, Runtimes & Models, Settings & Doctor use authenticated validated Core reads/mutations. Capabilities, Builds, Images, Instances are honest placeholder/unavailable screens. No jobs/event client. |
| Frontend server state | IMPLEMENTED subset | TanStack Query; explicit no retry; reads consume AbortSignal; no background health poll. Wire types are hand-maintained and runtime validation covers only system/runtime/model/job-pull responses. |
| Persistence | IMPLEMENTED foundation | SQLite WAL/foreign keys/busy timeout/Alembic; tables for runtimes, models, capabilities/sources, generic jobs/events, build jobs, artifacts, images/artifacts, instances, conversations/messages/memories/snapshots. Table existence does not prove product services/routes. |

## Accepted HTTP contract at snapshot

All routes require the per-launch bearer token. Canonical error envelope is `{error:{code,message,details,recoverable,actions}}`. OpenAPI/docs endpoints are disabled at runtime.

| Route | Status | Implemented truth | Frontend consequence/gap |
| --- | --- | --- | --- |
| `GET /api/v1/health` | IMPLEMENTED | Status/version/Python/PID/uptime. | Validated client exists. Version-compatibility policy is not defined. |
| `GET /api/v1/system/profile` | IMPLEMENTED | Bounded HardwareProfile. | Client exists. No refresh timestamp policy or resource lease data. |
| `GET /api/v1/system/doctor` | IMPLEMENTED | Bounded redacted diagnostic report/checks/issues/readiness. | Client exists. Recovery actions are text/data, not executable commands. |
| `GET /api/v1/runtimes` | IMPLEMENTED | Persisted runtime records. | Client exists. Endpoint is rendered; remote manual endpoints may be sensitive and need bounded display/copy. |
| `POST /api/v1/runtimes/refresh` | IMPLEMENTED | Runs bounded discovery and returns a terminal/running generic job record. | Client currently shows one notice, not event/history/reconciliation. |
| `POST /api/v1/runtimes/manual` | IMPLEMENTED subset | Validates absolute http(s), rejects embedded credentials, creates manual UNKNOWN record. | No OS credential reference contract; no test/probe-before-save contract. |
| `DELETE /api/v1/runtimes/{id}` | IMPLEMENTED | Manual records only; 204. | Current two-step inline confirmation exists. No reference-impact preview contract. |
| `GET /api/v1/models` | IMPLEMENTED | Filter params `runtime`, `capability`, `runnable`; persisted descriptors. Current repository implementation does not substantiate capability/runnable semantics beyond repository query. | Client exists; do not promise filters until verified by contract tests. Pagination/search absent. |
| `GET /api/v1/models/{model_key:path}` | IMPLEMENTED | Exact model descriptor. | Client exists; current UI does not use dedicated detail query. |
| `POST /api/v1/models/pull` | IMPLEMENTED | Ollama-only, explicit approval, disk/lease admission, persistent execution supervisor, bounded native stream, cancellation, post-pull discovery. Returns generic Job. | Current UI text is stale and says queue-only; future UI must re-baseline and show real execution without overstating post-pull confirmation. This is a current code/copy conflict requiring correction by the frontend owner after contract review. |
| `POST /api/v1/capabilities` | IMPLEMENTED | Creates DB draft plus canonical private workspace/default manifest with compensation. | No frontend client. `CapabilityRead.working_dir` exposes full host path and must not render; contract should remove/sanitize it. |
| `GET /api/v1/capabilities` | IMPLEMENTED | List ordered by update. | No pagination/search/summary counts; full host `working_dir` field. |
| `GET /api/v1/capabilities/{id}` | IMPLEMENTED | Basic record. | Same path exposure; insufficient editor detail. |
| `GET /api/v1/capabilities/{id}/detail` | IMPLEMENTED | Relative workspace plus sources, no document contents. | Preferred detail contract; runtime validator/client absent. |
| `PUT /api/v1/capabilities/{id}` | IMPLEMENTED | Metadata/manifest update with filesystem/DB compensation. | No concurrency token/version precondition; frontend cannot safely merge stale edits. |
| `GET /api/v1/capabilities/{id}/sources` | IMPLEMENTED | Saved sources. | No pagination; `local_path` is intended relative but contract should name it `relative_path`. |
| `POST /api/v1/capabilities/{id}/sources` | IMPLEMENTED subset | One behavior text, one explicitly approved PDF/Markdown/TXT path, or domain/regression evaluation JSONL; bounded atomic replacement. | Native picker absent. No training/tool/permission source types, batch ingest, source delete, upload progress, or parse job. |
| `POST /api/v1/capabilities/{id}/validate` | IMPLEMENTED | Real validator; bounded issues/provenance; detects manifest divergence. | Client/editor absent. No validation revision/digest for stale-report detection beyond timestamp. |
| `POST /api/v1/builds/analyze` | PARTIAL | Creates `BuildJob` in `DRAFT` with `ANALYSIS_NOT_STARTED`; does not analyze/baseline/plan. | UI must not show progress or a plan. Active T900 lane may supersede; re-baseline. |
| `GET /api/v1/builds/{id}` | IMPLEMENTED record read | Returns persisted row fields. | No list route, plan detail route, approval route, event link, report projection, or recovery contract. |
| `POST /api/v1/builds/{id}/cancel` | PARTIAL | Transitions nonterminal persisted build row to CANCELLED; no running execution at snapshot. | Cannot claim subprocess/resource cleanup. |
| `GET /api/v1/jobs/{id}` | IMPLEMENTED | Generic job snapshot. | No global list/filter/pagination or entity/operation summary. |
| `GET /api/v1/jobs/{id}/events` | IMPLEMENTED | Authenticated bounded persisted SSE page; `Last-Event-ID` accepts numeric or `jobs:<n>`; HTTP default/max 50; bounded/redacted events; connection closes after the page. | No frontend parser/client. Must use header-bearing fetch, resume and snapshot reconciliation; not native EventSource. |
| `POST /api/v1/jobs/{id}/cancel` | PARTIAL | Only model-pull jobs; terminal behavior effectively idempotent. | Cancellability must be explicit per job; route name/response has no typed capability flags. |
| `GET /api/v1/images` | IMPLEMENTED registry read | Basic image rows. | No frontend client; insufficient for runnable/integrity/artifact evaluation view. |
| `GET /api/v1/images/{digest}` | IMPLEMENTED registry read | Basic image row. | Same limitation; route order may collide with future subpaths unless carefully designed. |

## Specification routes not accepted on canonical snapshot

| Required route/contract | Status | Exact gap before UI |
| --- | --- | --- |
| `GET /builds/{id}/plan` | PROPOSED | Immutable typed plan, digest, reasons, estimates, blockers/warnings, approval requirements, revision/freshness. |
| `POST /builds/{id}/approve` | PROPOSED | Exact plan-digest grants, explicit consequences, expiry/invalidation, idempotency, execution job linkage. |
| `GET /builds` | PROPOSED | Paginated/filterable history and target summaries. |
| `GET /builds/{id}/baseline`, candidate evaluation/report routes | PROPOSED | Immutable report projections and bounded case pagination. |
| `GET /images/{digest}/evaluation` | PROPOSED | Exact suite/report/gate evidence. |
| Image artifact/knowledge/permissions/provenance/runnability detail | PROPOSED | One bounded detail projection or explicit subresources. |
| `POST /images/{digest}/verify` | PROPOSED | Durable job, integrity scope, report binding, no in-place mutation ambiguity. |
| `POST /images/{digest}/export` | PROPOSED | Picker-approved destination, codec/replace policy, limits, operation id, job/report. |
| `POST /images/import` | PROPOSED | Picker-approved archive, validation/registration plan, missing dependency result, atomicity/cleanup evidence. |
| `DELETE /images/{digest}` | PROPOSED | Confirmation, reference preflight, no cascade, artifact retention/GC semantics. |
| Instance create/list/get/start/stop/switch/rollback | PROPOSED | Exact runnable preflight, optimistic revision, durable session binding, error/recovery, idempotency. |
| Instance memory list/propose/approve/reject/reset | PROPOSED | Structured memory state, reset plan/token/audit, no secret content leakage. |
| Instance chat stream/history | PROPOSED | Authenticated bounded stream schema, persistence acknowledgment, resume/cancel/idempotency, provenance/source detail. |
| Knowledge source/snapshot/pipeline/retrieval APIs | PROPOSED | Durable job linkage, immutable snapshot detail, warnings, index/embedding identity, bounded excerpt and smoke-test evidence. |
| Evaluation list/detail/filter/case APIs | PROPOSED | Persisted report registry, suite identity, pagination, redaction/raw-output policy. |
| Jobs list/filter/cancellability/recovery | PROPOSED | Global Jobs depends on it; include cursor pagination, freshness, target link, `can_cancel`, `retry_mode`, operation identity. |
| Resource snapshot/policy/admission/lease APIs | PROPOSED | Internal governor is not application wiring. Define freshness, caps, persistence, and no UI override. |
| Observability events/sink health/export APIs | PROPOSED | Internal local/redacted primitives are not queryable or wired. Define pagination, retention gaps, and diagnostic-export content contract. |
| Settings read/update APIs | PROPOSED | Data/network/training/resource/log/privacy/developer/update settings need typed persistence and restart semantics. |
| Endpoint credential references | PROPOSED | OS credential store integration; UI gets reference/availability only, not bearer value. |
| Native picker commands | PROPOSED | Tauri dialog plugin/capabilities plus a minimal approved-path transfer contract. |
| Window state | PROPOSED | Safe persistence/restoration/clamping and tests; no authority to add plugin yet. |

## Internal Core capabilities that must not be mistaken for product APIs

| Domain | Status | Implemented foundation | Missing product integration |
| --- | --- | --- | --- |
| Runtime inference | INTERNAL_ONLY/PARTIAL product | Bounded Ollama/OpenAI-compatible streaming, exact binding, tool-request parsing, cancellation/errors. | Instance/chat service/DB/API; active runtime-native-tools lane may add optional tool schemas. |
| Hardware/planning/resources | INTERNAL_ONLY plus profile API | Hardware profile; deterministic planner/resource estimates; strict resource governor/leases/snapshots. | Build execution wiring; resources/settings APIs; observation history. |
| Knowledge | INTERNAL_ONLY | Bounded intake, normalization, chunking, embeddings protocol, snapshots, retrieval; Docling/LanceDB providers accepted but not live-proven. | Persisted pipeline orchestration/jobs/API/UI; real provider availability states. |
| Evaluation | INTERNAL_ONLY | Pure scorers, aggregation, gates, held-out leakage checks. | Runtime execution, report persistence/API/case redaction. |
| Builds | INTERNAL_ONLY/PARTIAL API | Pure lifecycle, approvals, workspaces, runners; basic DB row routes. | Dependency-complete runner/orchestration, plan/approve/report/image finalization. Active lane unaccepted. |
| Training | INTERNAL_ONLY | Bounded MLX provider execution and cancellation with injected-process tests. | Build orchestration/API/UI; real provider/model proof intentionally absent. |
| Images/OCI | INTERNAL_ONLY plus basic registry API | Strict typed image config, OCI layout, bounded archive/import validation. | Finalization, artifact graph API, verify/export/import/delete application services/routes. |
| Portability | INTERNAL_ONLY | Export/import services, guards, limits, atomic intentions tested. | Product API/native path approval/jobs/DB registration integration. Active lane unaccepted. |
| Instances/memory/tools | INTERNAL_ONLY | Exact identity/runtime selection, lifecycle/chat/memory/reset/tool permission primitives. | Persistent service/router/main wiring; active lane unaccepted. |
| Observability | INTERNAL_ONLY | Bounded frozen structured events, redaction, local memory/JSONL sinks; telemetry disabled. | Event emission wiring, API/UI, sink health and diagnostic export. |

## Contract conflicts and decisions

1. **Current Models copy vs implemented pull execution.** `RuntimesModelsView` says the Core only persists a queue plan and downloads no bytes, but canonical Core now executes bounded model pulls and confirms discovery. Canonical code/handoff wins. The future frontend must replace the stale text after direct contract review; until then it is a known production-truth defect.
2. **Current README/product docs vs canonical progress.** Repository docs still describe only health or early foundations in places. Accepted code/tests/handoffs are newer. Do not use stale documentation to disable implemented APIs.
3. **Spec SSE language vs canonical snapshot pages.** The specification says SSE streams progress. Canonical endpoint emits one bounded persisted page and closes. The frontend loops page/reconcile/backoff; a truly held-open stream would be a new contract.
4. **Spec Build Plan route vs canonical build API.** Canonical analyze is intentionally nonexecuting. No frontend animation or optimistic plan is allowed until the active build lane is integrated and reverified.
5. **Spec native file UX vs current Tauri permissions.** The UI spec expects drag/drop/pickers/export/import. Current Tauri lacks dialog/fs commands. Browser-only workarounds would cross the intended boundary and are forbidden.
6. **Spec generated client vs disabled OpenAPI/manual types.** Create a committed, reviewed contract-generation artifact or maintain one single manual wire owner temporarily. Do not expose runtime docs/OpenAPI simply to satisfy the frontend without a security review.
7. **`CapabilityRead.working_dir` path exposure.** The basic response leaks a host path inconsistent with frontend path-redaction policy. Core must replace/deprecate it or the client must runtime-validate then discard it; it is never user-visible.
8. **Settings/Resources/Observability aspirational surfaces.** Strong internal primitives exist, but absence of APIs is not an excuse to read files/direct DB from Tauri/React. These screens remain honest unsupported states until contract owners close the gaps.

## Required shared contract conventions

Before frontend implementation, the single shared-contract owner must define:

- Stable API/schema version and minimum-compatible frontend/Core handshake.
- Cursor pagination for lists; maximum page sizes and sort stability.
- `observed_at`/`updated_at` freshness for every status projection.
- Operation/idempotency id for consequential mutations and uncertain-submit recovery.
- Explicit `can_cancel`, `can_retry`, `retry_mode`, `recovery_actions`, and related entity links on job/build projections.
- Exact typed error codes/actions; details schema per endpoint or discard-by-default.
- Optimistic concurrency revision/ETag for mutable Capability/Settings/Instance operations.
- Safe path projections (`basename`, relative workspace id) instead of host paths.
- Raw-content disclosure/redaction bounds for eval output, logs, excerpts, metadata, and chat provenance.
- Approval objects bound to target digest/plan revision, scope, consequence, time, and invalidation.
- Streaming framing, byte/event limits, cursor semantics, heartbeat/no-new-event semantics, terminal reconciliation, and cancellation separation.
- File picker approval transfer, symlink/size/type revalidation, destination replace policy, and cleanup evidence.

## Backend/API gap priority

| Priority | Gap | Why it blocks frontend |
| --- | --- | --- |
| P0 | Integrate/freeze build, evaluation, image, instance/chat/memory, portability routes and shared schemas | Core product workflows cannot be truthfully operated without them. |
| P0 | Stable job list + typed event client contract + operation identity/cancellability | Every long workflow and global Jobs depends on it. |
| P0 | Native picker/approved-path boundary and OS credential reference boundary | Capability ingestion and export/import cannot be production-safe otherwise. |
| P0 | Remove/sanitize host paths and define error/detail redaction | Prevents local privacy leakage. |
| P1 | Knowledge pipeline/snapshot/retrieval inspection routes | Capability/build/chat provenance cannot be inspected. |
| P1 | Resource/admission/lease projections and settings persistence | Heavy actions cannot explain safety decisions or operator policy. |
| P1 | Image detail/runnability/reference preflight | Run/export/delete decisions otherwise guess. |
| P1 | Optimistic concurrency and idempotency | Prevents stale overwrites and duplicate mutations. |
| P2 | Observability event/sink/diagnostic-export routes | Needed for production recovery and support, but not to start basic contract primitives. |
| P2 | Window restoration/updater | Release quality; must remain isolated from core lifecycle and offline promise. |

## Re-baseline gate for the future Kimi run

Before writing frontend code, Kimi must capture current canonical SHA and regenerate this ledger from registered routes, schemas, migrations, source tests, and accepted handoffs. For each `PROPOSED` item that has landed, record exact endpoint/request/response/error/event tests. For each still missing item, keep the corresponding UI unavailable and route a disjoint backend contract task. No frontend worker may cross the gap.
