# Screen Architecture, User Flows, and State Machines

This file specifies user-visible behavior. `contract-ledger.md` decides whether the behavior is implemented, proposed, or blocked. No proposed state may be displayed as if Core already supplies it.

## Shared interaction rules

- A destination opens with a stable heading, last-confirmed/freshness state, scoped actions, and a content landmark.
- List/detail selection is route-addressable by nonsecret stable id/digest. Filters may be URL state if they contain no private data.
- Reads preserve stale confirmed data during refresh and label it. Mutations invalidate only affected queries.
- Toasts report noncritical acknowledgments. Validation, permissions, destructive impact, job failure, and recovery remain in the owning surface.
- Every long-operation call returns or resolves to a durable job/build id before the UI claims it started.
- Event disconnect is a presentation state. Terminal status comes only from the canonical job/build snapshot.
- Error envelopes render the safe `code`, `message`, `recoverable`, and mapped actions. Unknown `details` are not rendered.

## 1. Dashboard

### Purpose and structure

Answer: Is Core healthy? Which real runtimes/models are usable? What durable work is active? What blocks the next meaningful action?

Sections: Core/local boundary; readiness facts; active jobs; attention items; recent accepted local events; primary next action. Counts appear only from real list/summary contracts with timestamps.

### Flow

`open -> resolve Core -> read profile/doctor/runtimes/models/(future images/instances/jobs) -> render readiness -> navigate to exact remediation or workflow`

### State machine

`CORE_STARTING -> SUMMARY_LOADING -> READY_EMPTY | READY | READY_LIMITED`

Alternatives: `CORE_FAILURE`, `PARTIAL_ERROR`, `STALE`, `OFFLINE_LOCAL_OK`.

- Loading: independent Core and summary states; no all-or-nothing spinner after Core connects.
- Empty: no runtimes/models; offer scan/refresh and manual endpoint, never forced pull.
- Partial: show confirmed sections, mark failed sections unavailable, one retry per source.
- Attention: doctor failures, stale discovery, interrupted jobs, disk/resource denial, imported image missing Base Model.
- Offline: local operations remain available; acquisition/remote endpoints state why blocked.
- Recovery: Core restart only through Tauri; no repeated loop. Job recovery routes to Jobs, not a fake dashboard completion.

## 2. Models and runtimes

### Structure

Tabs or segmented views: `Base Models`, `Runtimes`, `Acquisition`. Model list/detail shows exact key, runtime, identity strength, digest, family, format, quantization, size, context, capabilities, last seen, and only bounded returned metadata. Runtime detail shows kind, source, endpoint summary, vendor evidence, installed/server status, warnings, and last probe.

### Discovery flow

`catalog load -> Refresh -> Core bounded discovery job -> runtime/model snapshot refresh -> selected record reconciliation`

States: `NOT_SCANNED`, `DISCOVERING`, `EMPTY_NO_RUNTIME`, `RUNTIME_NO_MODELS`, `POPULATED`, `PARTIAL_PROBE_FAILURE`, `INVALID_RESPONSE`, `STALE`.

- Invalid services on known ports are not labeled as vendors.
- Executable found/API absent becomes installed/not running, with instructions. No automatic start.
- Manual endpoint: `editing -> inline URL/kind validation -> submit -> UNKNOWN record -> explicit Refresh -> ONLINE/OFFLINE/ERROR`.
- Credentials cannot be embedded. Until an OS credential reference contract exists, manual bearer-token UI is blocked.
- Delete exists only for manual config, previews affected model records, confirms, then reconciles. Auto runtime records cannot be deleted.

### Acquisition flow

`select ONLINE Ollama runtime -> enter conservative model reference -> size known/provided -> explain native pull/network/disk -> explicit approval -> job accepted -> Jobs event pages + snapshot -> cancel request or terminal -> mandatory discovery confirmation`

States: `UNSUPPORTED_RUNTIME`, `APPROVAL_REQUIRED`, `DISK_UNKNOWN`, `DISK_INSUFFICIENT`, `LEASE_CONFLICT`, `QUEUED`, `DOWNLOADING_INDETERMINATE`, `DOWNLOADING_DETERMINATE`, `VERIFYING_DISCOVERY`, `SUCCEEDED_CONFIRMED`, `FAILED`, `CANCEL_REQUESTED`, `CANCELLED`, `INTERRUPTED_RECOVERABLE`.

- `SUCCEEDED_CONFIRMED` requires post-pull exact discovery; job success alone does not prove installation.
- If total bytes are absent, show phase/received evidence without percentage.
- Retry creates a new acquisition only after reconciling the prior job and exact runtime/model state.

## 3. System Doctor

### Structure

Aggregate health, generated time/budget, check list, redacted evidence, issues, recovery actions, feature readiness, and hardware profile. "Run doctor" is a bounded read in the current contract, not a long job.

### State machine

`IDLE -> RUNNING_CHECKS -> HEALTHY | LIMITED | FAILED`

Alternatives: `CHECK_TIMEOUT/UNAVAILABLE`, `CORE_FAILURE`, `STALE_REPORT`.

- Each check has pass/warn/fail/unavailable/skipped text and semantic icon.
- Missing optional provider blocks only its feature. Core-start blockers are visually distinct.
- Recovery actions are instructions until an exact mutation contract exists; never execute shell commands from diagnostic text.
- Evidence renders only known scalar/basename/digest-prefix/presence/notes fields. Raw paths, commands, environment, or exceptions are forbidden.
- Unsupported hardware offers safe fallback (for example no training) rather than treating the whole application as broken.

## 4. Capabilities

### Structure

Catalog plus editor tabs: Overview, Behavior, Knowledge, Training, Tools, Permissions, Evaluations. Header shows saved version, unsaved status, validation status/time, source counts from real records, and current manifest identity.

### Draft flow

`New -> name/version -> Core creates canonical workspace/default manifest -> edit -> explicit Save -> canonical response -> Validate -> VALID | INVALID`

State machine: `NEW -> SAVING -> SAVED_UNVALIDATED -> VALIDATING -> VALID | INVALID`; side states `DIRTY`, `SAVE_CONFLICT`, `WORKSPACE_DIVERGED`, `ROLLBACK_UNCONFIRMED`, `PERMISSION_REQUIRED`, `UNSUPPORTED_SOURCE`.

- Do not claim a capability is reusable/verified; Capability Source is editable input.
- The current API can create/update metadata/manifest and add behavior/document/evaluation sources. Training examples, tool manifests, permission policy editors, source removal, capability delete/archive, and directory import remain contract gaps.
- `working_dir` from the current list/read response is a sensitive full path and must never render. Use detail `workspace_relative` after Core contract repair.
- Unsaved edits: navigation prompts Save/Discard/Stay. Discard affects only frontend edits, never saved Core files.
- Save conflict: refresh and show bounded field/manifest difference; no blind overwrite.

### Source ingestion

`native picker -> selected basename/type/size -> explicit approval -> Core bounded copy/hash -> saved source -> validate`

States: `SELECTED`, `COPYING`, `SAVED_HASHED`, `REPLACED_AT_SAME_ROLE`, `UNSUPPORTED`, `TOO_LARGE`, `UNREADABLE`, `SYMLINK_REJECTED`, `MANIFEST_DIVERGED`, `PUBLICATION_FAILED_PRIOR_PRESERVED`, `ROLLBACK_UNCONFIRMED`.

- Routine validation stays inline with file/line when safe.
- A failure states whether the prior saved source remains intact.
- Ingestion never parses arbitrary HTML in the webview or modifies the original.

## 5. Knowledge

### Structure

Capability-scoped Sources; immutable Snapshot detail; Conversion warnings; Chunks/provenance; Embedding/index identity; Retrieval smoke tests/evidence. It is a dedicated inspector linked from Capabilities, Builds, Images, Instances, and chat sources.

### Pipeline state machine

`APPROVED_SOURCES -> HASHING -> PARSING -> NORMALIZING -> CHUNKING -> EMBEDDING -> INDEXING -> RETRIEVAL_TEST -> SNAPSHOT_READY`

Terminals/side states: `CANCEL_REQUESTED`, `CANCELLED`, `SOURCE_REJECTED`, `PARSER_UNAVAILABLE`, `OCR_APPROVAL_REQUIRED`, `PARSED_WITH_WARNINGS`, `EMBEDDING_PROVIDER_MISSING`, `EMBEDDING_IDENTITY_CHANGED`, `INDEX_BACKEND_UNAVAILABLE`, `INDEX_CORRUPT`, `RETRIEVAL_TEST_FAILED`, `FAILED_CLEANUP_REQUIRED`.

- No API currently exposes this pipeline or snapshot inspection. The UI remains blocked until typed services/routes and persistence exist.
- OCR is explicit, slow, and quality-affecting. Do not auto-enable.
- Snapshot identity changes when source hash, breaking parser/chunker config, or embedding identity changes. UI labels old snapshot immutable/stale for this draft; it never mutates it.
- Chunk/excerpt views are bounded and treated as private untrusted text. Stable source id, title, page/section, digest, and warnings remain visible.
- Retrieval results show scores only when Core returns defined units/meaning; never infer confidence percentages.
- Offline: existing local snapshot/retrieval remains usable; missing local embedding provider blocks rebuild only.

## 6. Builds

### Structure

Build catalog; new-build wizard; analysis; immutable plan; approval review; durable timeline; artifacts/checkpoints; terminal report. Keep capability, exact model identity, runtime, hardware snapshot, policy, and plan digest visible.

### Canonical build state machine

`DRAFT -> ANALYZING -> BASELINE_RUNNING -> PLANNED -> ACQUIRING_APPROVED_ARTIFACTS -> BUILDING_KNOWLEDGE -> TRAINING_ADAPTER? -> MATERIALIZING -> EVALUATING -> PACKING -> VERIFIED`

Terminal alternatives: `BLOCKED`, `FAILED`, `CANCELLED`, `VERIFICATION_FAILED`.

The current canonical `POST /builds/analyze` records a `DRAFT` with `ANALYSIS_NOT_STARTED`; it does not run analysis. Until the execution contract lands, the UI must say "recorded, analysis unavailable," not animate phases.

### Wizard flow

1. Select validated Capability Source.
2. Select currently discovered exact Base Model/runtime.
3. Configure policy: strategy mode, network/acquisition, training preference/allowance, disk/memory limits, verification requirement.
4. Submit analysis and follow its durable identity.
5. Inspect real baseline, compatibility, resources, strategy reasons, required artifacts/downloads, permissions, warnings/blockers.
6. Approval binds to exact immutable plan digest and disclosed consequences.
7. Start execution; monitor from this screen or Jobs.

### State behavior

- Loading: selected inputs remain visible; baseline phase never claims candidate work.
- Validation: missing/invalid capability/model/policy is inline; runtime drift becomes `BLOCKED` and invalidates plan/approval.
- Permission: separate grants for downloads, training, permissions, and disk estimate if Core requires them. A plan change invalidates all grants.
- Unsupported: adapter incompatibility or missing provider offers planner-approved nontraining fallback, not UI-selected silent downgrade.
- Offline: analysis may proceed only if all inputs local and Core permits; acquisition/training that needs artifacts is blocked before start.
- Streaming: timeline is built from persisted events/checkpoints; details show bounded redacted events.
- Cancel: confirm that previous immutable artifacts remain and partial candidate is unusable; show request/acknowledgement/cleanup.
- Retry/recovery: resume only from Core-declared checkpoint with same identity; otherwise `Rebuild as new job`. Never rewrite history.
- Verification failure: comparison remains inspectable, candidate is unverified, previous image unchanged, no success color or Run-as-verified shortcut.
- Failure: show failed phase, safe retained checkpoints, cleanup state, and exact next allowed action.

## 7. Evaluations

### Structure

Filters for capability/image/model/status/date; real report list; baseline/candidate comparison; gate matrix; suite identity; reproducibility settings; case results; failed cases; performance facts with units.

### State machine

`NOT_RUN -> BASELINE_RUNNING -> BASELINE_READY -> CANDIDATE_RUNNING -> COMPARISON_READY -> GATES_PASS | GATES_FAIL`

Side states: `SUITE_INVALID`, `HELDOUT_LEAKAGE_BLOCKED`, `RUNTIME_DRIFT`, `CANCEL_REQUESTED`, `CANCELLED`, `PARTIAL_RESULTS`, `FAILED`.

- Scores show numerator/denominator, metric/scorer, suite digest/version, exact environment, and observed delta. "Better" is never inferred across unlike suites/configurations.
- Empty: explain evaluation runs inside builds; offer new build only when contracts/prerequisites allow.
- Failed cases are expandable, bounded, and redact private content per contract. Raw model output needs a deliberate reveal/copy affordance and local-only warning.
- Gate outcome is pass/fail per rule; status is not averaged into a decorative overall percentage.
- Current canonical has evaluation domain primitives but no evaluation API/report persistence routes. Entire destination is blocked until contract gaps close.

## 8. ZANA Images

### Structure

Registry columns: name/version, digest, verification status, exact Base Model requirement, runnable status, build strategy, created. Detail tabs: Overview, Artifacts, Knowledge, Adapter, Permissions, Evaluation, Build provenance, Integrity.

### Image lifecycle

`REGISTERED_UNVERIFIED | VERIFIED_LOCAL | VERIFIED_REPRODUCIBLE | VERIFICATION_FAILED`

Orthogonal runnability: `RUNNABLE`, `MISSING_BASE_MODEL`, `MODEL_DIGEST_MISMATCH`, `RUNTIME_UNAVAILABLE`, `MISSING_EMBEDDING`, `ADAPTER_INCOMPATIBLE`, `CORRUPT`, `UNSUPPORTED_SCHEMA`, `UNKNOWN_UNVERIFIED`.

- Immutable fields are never editable. A rebuild creates a new image/digest.
- Current list/detail only exposes the registry row. Artifact graph, plan/evaluation, integrity, runnability, export, import, verify, delete, and reference count contracts are missing in canonical.
- Verify again creates durable evidence and never rewrites the original report/digest.
- Delete flow: preflight references -> blocked if any Instance references -> show local registration/artifact impact -> explicit confirmation -> job/atomic result -> reconcile. Never cascade Instances.
- Corruption: disable Run/Export, show integrity failure, preserve evidence, offer re-import/rebuild if safe.
- Missing model: image remains imported/inspectable and cannot run; offer refresh or explicit supported acquisition, never name-match guessing.

## 9. Instances and chat

### Structure

Instance catalog and detail tabs: Chat, Memory, Runtime, Image history, Provenance/Events. Show mutable status, active immutable image digest, exact runtime/model binding, resource/readiness state, and last transition.

### Create/start/stop

`SELECT_IMAGE -> PREFLIGHT -> CREATE_STOPPED -> STARTING -> RUNNING -> STOPPING -> STOPPED`

Alternatives: `NOT_RUNNABLE`, `RUNTIME_DRIFT`, `MISSING_ARTIFACT`, `MISSING_SECRET_REFERENCE`, `RESOURCE_BLOCKED`, `ERROR`, `CLEANUP_UNCERTAIN`.

- Create does not imply start. Start validates image blobs, exact model/runtime, knowledge, permissions, embedding, adapter materialization, and resources.
- Start/stop are idempotent only if Core contract says so. Cleanup uncertainty blocks replacement/start.
- Offline is normal when all dependencies local and image denies network.

### Chat stream

`READY -> SENDING -> MESSAGE_START -> RETRIEVAL* -> TOOL_REQUEST? -> TOOL_RESULT? -> TOKEN* -> MESSAGE_END`

Alternatives: `CANCEL_REQUESTED`, `CANCELLED_PARTIAL`, `RUNTIME_DISCONNECTED`, `PERMISSION_DENIED_TOOL`, `INSUFFICIENT_EVIDENCE`, `CONTEXT_TRUNCATED`, `TIMEOUT`, `ERROR`.

- User message is persisted/acknowledged before showing authoritative sent state.
- Token display is batched. Partial answer is labeled partial and never silently promoted after error/cancel.
- Sources/tool/image pills appear only from stored provenance. Source drawer shows exact returned document/page/section/excerpt.
- Tool requests require Core permission decision; the UI may present a required approval only if contract supports it and never executes the tool.
- Context truncation is disclosed with policy; system permissions are never shown as dropped.
- Retry after uncertain send first refreshes conversation; avoid duplicate messages/inference.

### Memory and image switch

- Proposal: `PENDING -> APPROVED | REJECTED`; no autonomous unlimited memory.
- Reset scopes: chat only, approved memory, full instance. Each previews exact scope and requires confirmation; result includes audit/recovery evidence.
- Switch image: `PREFLIGHT -> SNAPSHOT -> COMPATIBILITY_CHECK -> SMOKE_CHECK -> SWITCHED`; failure keeps old pointer. Rollback restores pointer/snapshot but explicitly cannot undo external side effects.
- No canonical instance/chat/memory API is registered today; the UI stays blocked until typed routes and persistence integration are accepted.

## 10. Jobs

### Structure

Global active/history list with kind, target summary, canonical status, phase, trustworthy progress, start/update/end, owner/reference, cancellation/recovery. Detail includes snapshot, persisted event timeline, error, resource lease, and related entity links.

### Event client state machine

`LOAD_SNAPSHOT -> FETCH_EVENT_PAGE(cursor) -> APPLY_BOUNDED_EVENTS -> RECONCILE_SNAPSHOT -> TERMINAL | WAIT_BACKOFF -> FETCH_NEXT_PAGE`

Client side states: `CONNECTED_PAGE`, `NO_NEW_EVENTS`, `DISCONNECTED_JOB_RUNNING`, `CURSOR_REJECTED`, `AUTH_FAILED`, `STREAM_EVENT_TOO_LARGE`, `STALE_SNAPSHOT`.

- Current `/jobs/{id}/events` is a bounded persisted snapshot page, not an open real-time feed. Default 50, max 50 at HTTP boundary; service hard cap 100; event max 4 KiB and page byte budgets are enforced.
- Native `EventSource` is forbidden because current bearer auth requires a header. Use authenticated fetch streaming and exact `Last-Event-ID`.
- There is no canonical list-jobs endpoint. Global Jobs cannot ship until list/filter/pagination/freshness/target summary contracts exist.
- Current generic cancel accepts only model-pull jobs; builds use `/builds/{id}/cancel`. UI shows Cancel only for exact cancellable kinds/states returned by contract.
- Closing event consumption never sends cancellation.
- Retry transport keeps cursor. Cursor reset requires explicit full snapshot/history recovery, not silent duplicate application.

## 11. Observability

### Structure

Local event search/filter, operation/job/instance/image correlation, severity, phase, timestamp, recovery code, bounded redacted payload, sink health/retention, Core log location summary, and optional diagnostic export review.

### State machine

`NO_API -> LOADING -> EMPTY | POPULATED -> FILTERED`; side states `SINK_UNAVAILABLE`, `RETENTION_TRUNCATED`, `ROTATION_UNCERTAIN`, `READ_ERROR`, `EXPORT_REVIEW`, `EXPORT_JOB`, `EXPORT_FAILED`.

- Canonical observability primitives are local, bounded, redacted, telemetry-disabled, and can write memory/JSONL sinks, but they are not wired to application events or an authenticated API.
- No UI exists until a bounded pagination/cursor/schema/redaction contract is accepted.
- Do not render full paths or raw payload. Private content is redacted by Core; frontend never offers a "show unredacted" bypass.
- Retention gaps are explicit. Empty does not mean no incident if sink is unavailable/truncated.
- Diagnostic export previews included categories, excludes secrets/documents by default, requires native save approval, and reports digest/size; this contract is missing.

## 12. Resources

### Structure

Current host snapshot; policy/reserves; operation-category limits; active leases; recent admission decisions/usage; pressure/recovery guidance. Every value has units, observation time, and source/error state.

### State machine

`NO_API -> SNAPSHOT_LOADING -> SNAPSHOT_READY | SNAPSHOT_PARTIAL | SNAPSHOT_UNKNOWN`

Operation admission: `REQUESTED -> ADMITTED_LEASED -> RELEASED | CANCELLED | EXPIRED`; denial: `BLOCKED(reason, recovery actions)`.

- Canonical resource governor is dependency-free, conservative, bounded, and has no background sampler, but it is not wired into app state/API/UI.
- Unknown headroom blocks heavy actions; UI cannot treat it as zero or infinite.
- Policy editing requires a persisted settings/validation contract and explicit effect scope. Current UI must be read-only/unavailable.
- No fabricated trend charts. If future sampling exists, expose exact cadence/retention and gaps.
- A lease conflict links to the owning job when safe. UI never force-releases a lease without a dedicated reviewed Core operation.

## 13. Settings

### Structure

Sections: Data location; Runtime endpoints/credential references; Network policy; Training/resource limits; Logs/retention; Privacy; Accessibility/density; Developer; Updates; About/version.

### State machine

`LOAD -> READY -> DIRTY -> VALIDATING -> SAVING -> SAVED`

Alternatives: `INLINE_INVALID`, `RESTART_REQUIRED`, `CONFLICT`, `PERMISSION_REQUIRED`, `UNSUPPORTED_PLATFORM`, `SAVE_FAILED_PRIOR_PRESERVED`.

- Current canonical exposes profile/doctor and runtime manual endpoints, not a settings service. Do not build controls that persist only in browser storage and look authoritative.
- Data root cannot be casually edited. A future relocation flow must preflight capacity, stop/confine writers, copy/verify atomically, retain rollback, and restart Core.
- Network default is deny after acquisition. Per-feature change shows scope and never enables remote telemetry.
- Training/resource settings are bounded by Core policy; invalid relaxation is rejected.
- Telemetry is shown `Off` and noneditable in V1.
- Developer mode may show API version/port and redacted probes; never token or raw environment.
- Updates are optional, explicit, signed, and independent of cloud-account/telemetry. Current updater contract/plugin is absent.

## 14. Export and import

### Export flow

`select image -> reference/integrity preflight -> choose native destination -> review included immutable roles/exclusions -> confirm -> IMAGE_EXPORT job -> verify archive digest + sidecar report -> success/reveal destination summary`

States: `IMAGE_NOT_VERIFIED_OR_CORRUPT`, `DESTINATION_PERMISSION_DENIED`, `DISK_UNKNOWN/INSUFFICIENT`, `CODEC_UNAVAILABLE`, `QUEUED`, `PACKING`, `WRITING`, `VERIFYING`, `CANCEL_REQUESTED`, `CANCELLED_CLEAN`, `FAILED_PRIOR_ARCHIVE_PRESERVED`, `FAILED_PARTIAL_CLEANED`, `CLEANUP_UNCERTAIN`, `SUCCEEDED`.

- Export excludes secrets and mutable Instance state. Model weights remain excluded by default.
- Existing destination replacement requires a separate preflight/atomic replace token; never truncate on picker selection.
- Success shows OCI format, image digest, archive digest, size, and report; no claim of runnability elsewhere.

### Import flow

`native archive pick -> bounded temporary unpack -> OCI/schema/digest/path validation -> dependency/runnability check -> registration plan review -> atomic blob/image registration -> cleanup -> report`

States: `SELECTED`, `VALIDATING`, `CORRUPT_REJECTED`, `TRAVERSAL_REJECTED`, `SYMLINK_REJECTED`, `SECRET_REJECTED`, `UNSUPPORTED_SCHEMA`, `CODEC_UNAVAILABLE`, `DUPLICATE_IDENTICAL`, `CONFLICT`, `MISSING_BASE_MODEL_IMPORTABLE`, `RUNTIME_INCOMPATIBLE_IMPORTABLE`, `DISK_BLOCKED`, `REGISTERING`, `CANCEL_REQUESTED`, `CANCELLED`, `FAILED_ROLLED_BACK`, `CLEANUP_UNCERTAIN`, `SUCCEEDED_RUNNABLE`, `SUCCEEDED_NOT_RUNNABLE`.

- Missing Base Model does not reject a valid image; register as not runnable with exact dependency.
- Same display name/different digest never counts as compatible.
- Duplicate identical digest is idempotent and reports existing registration; conflicting content for an existing digest is corruption.
- Import never starts an Instance, runtime, pull, rebuild, or tool.
- Current canonical portability/image primitives exist but no registered product API/native dialog/client exists. Entire workflow remains blocked pending exact contracts.

## Cross-screen completion matrix

| Requirement | Owning screen(s) | Proof |
| --- | --- | --- |
| Real local runtime/model discovery | Models, Dashboard, Doctor | Actual runtime response, exact descriptor, refresh reconciliation. |
| Capability save/reopen | Capabilities | Canonical persisted draft/workspace/detail plus validation evidence. |
| Knowledge provenance offline | Knowledge, Builds, chat source drawer | Snapshot identity, retrieval result, offline run evidence. |
| Baseline/candidate/gates | Builds, Evaluations, Images | Immutable reports bound to exact identities; failed gate blocks image verification. |
| Long progress/cancel/recovery | Jobs plus contextual views | Persisted snapshots/events and acknowledged cancellation. |
| Immutable/mutable separation | Images, Instances | No image edit/memory export leakage; pointer/snapshot switch semantics. |
| Resource safety | Resources, all heavy-action approvals | Core admission/lease evidence and blocked unsafe work. |
| Privacy/security | Settings, Doctor, Observability, every picker/approval | Token/secret/path redaction, deny defaults, native/scoped boundaries. |
| Safe OCI round trip | Export/Import, Images, Jobs | Same image digest, archive verification, corrupt rejection, missing model state. |

No screen is accepted from appearance alone. Each must demonstrate its state machine against real Core contracts and the test matrix in `delivery-verification.md`.
