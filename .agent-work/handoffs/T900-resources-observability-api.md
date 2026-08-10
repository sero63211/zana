# T900 Resources + Observability API Handoff

Verdict: PASS

## Preflight

- Isolated worktree: `/Users/sero/.codex/worktrees/eadd/zana`
- Branch: `agent/t900-resources-observability-api`
- Base: `52bc1be7908fb4956cc09f5192ac83f5aa8f2310` (clean detached HEAD verified
  before writing; lead GO confirmed canonical main at
  `aa3691d507dc12dcbb70e9be348002cbb8dccf4f`)
- Remote: `origin https://github.com/sero63211/zana.git`
- Exclusive ownership confirmed by lead; no other active writer owns any path
  touched here.

## Scope implemented

Dependency-complete Core services plus isolated authenticated routers for the
bounded resource and local observability surface. No shared schema, main
registration, DB model/repository/UoW/migration, job, desktop/Tauri, manifest,
or lockfile was edited.

Owned changes:

- `core/zana_core/resources/service.py` - thread-safe `ResourceService` over
  the accepted `ResourceGovernor`: snapshot with explicit captured-at/age/fresh
  state, explicit `refresh()`, typed policy projection, active leases, bounded
  descending usage pages with exclusive `before_sequence` cursors, and
  locked `admit`/`release` wrappers for future writers.
- `core/zana_core/observability/registry.py` - thread-safe
  `ObservabilityRegistry`: writes only to injected local sinks, retains only
  after at least one sink accepts an already-redacted event, bounded retention
  with explicit drop counters, descending event page cursors, and live sink
  health with `telemetry_enabled=false` and `remote_transport=none`.
- `core/zana_core/api/resources.py` - authenticated router
  `GET /api/v1/resources/snapshot`, `POST /api/v1/resources/snapshot/refresh`,
  `GET /api/v1/resources/policy`, `GET /api/v1/resources/leases`,
  `GET /api/v1/resources/usage`.
- `core/zana_core/api/observability.py` - authenticated router
  `GET /api/v1/observability/events`, `GET /api/v1/observability/health`.
- `core/zana_core/api/resources_schemas.py`,
  `core/zana_core/api/observability_schemas.py` - strict local API projections.
- Package exports in `core/zana_core/resources/__init__.py` and
  `core/zana_core/observability/__init__.py`.
- `core/tests/resources/test_service.py`,
  `core/tests/observability/test_registry.py` - focused service tests.
- `core/tests/api/test_resources_observability.py` - the single new
  resource-observability API test file (auth, 503, redaction, freshness,
  unknown state, typed policy/leases/usage, cursor pagination, retention,
  telemetry-off, unsupported JSONL, invalid bounds).

## Exact typed projections and states

- Snapshot: `revision`, `captured_at`, `age_seconds`, `fresh`, platform,
  os/arch, cores, memory total/available, redacted `disk_path`, disk free,
  fixed `probe_error_code` (`MEMORY_PROBE_UNAVAILABLE`,
  `DISK_PROBE_UNAVAILABLE`, `SNAPSHOT_PROVIDER_UNAVAILABLE`,
  `PROBE_UNAVAILABLE`) and `probe_status` (`ok`/`partial`/`unavailable`).
  Unknown fields stay `null`; raw probe exceptions and full host paths never
  leave the service.
- Policy: strict typed categories with exact caps; `revision` matches the
  accepted governor lease policy revision constant.
- Leases/usage: exact `ResourceLease`/`UsageRecord` fields only; no fabricated
  timestamps or history.
- Events: newest-first pages of already-redacted local records, re-redacted
  before projection; `kind`, `severity`, `timestamp`, `message`, `context`,
  `payload`, `line_bytes`, `received_at`, and `invalid` fail-closed fields.
- Cursor semantics: `before_sequence` is exclusive; `next_cursor` is the last
  returned sequence when more older records exist, else `null`; `truncated`
  and `total_available` are explicit; registry retention exposes
  `retention_dropped`.
- Sink health: memory/JSONL presence, availability, stats, bounds, redacted
  `log_root`, `mode` (`local_memory_jsonl`/`local_memory`/`local_jsonl`/
  `disabled`), and explicit `NOT_CONFIGURED` or `PLATFORM_UNSUPPORTED` JSONL
  state.

## Checks run and evidence

All commands used the existing shared venv at
`/Users/sero/Documents/zana/core/.venv/bin`; nothing was installed, no model,
runtime, network, browser, app, bundle, DB persistence, or filesystem export
was exercised.

| Check | Command | Result |
| --- | --- | --- |
| New focused tests | `pytest core/tests/resources/test_service.py core/tests/observability/test_registry.py core/tests/api/test_resources_observability.py` | 31 passed |
| Owned suites | `pytest core/tests/resources core/tests/observability core/tests/api/test_resources_observability.py` | 231 passed |
| Adjacent API/streaming | `pytest core/tests/api core/tests/streaming` | 232 passed; 4 pre-existing unrelated capability-authoring failures (see residual risk) |
| Ruff lint | `ruff check` owned source/tests | clean |
| Ruff format | `ruff format --check` owned source/tests | clean |
| Pyright | `pyright` owned implementation modules | 0 errors, 0 warnings |
| Import smoke | `import zana_core.resources, zana_core.observability, zana_core.api.resources, zana_core.api.observability` | pass |
| Diff hygiene | `git diff --check` | pass |
| Clean proof after product commit | `git status --porcelain` | empty |

## Security delta

- Every new endpoint requires the exact per-launch bearer token through the
  canonical router-level `verify_token` dependency; missing/wrong tokens return
  the canonical 401 envelope.
- No raw host paths, secrets, private content, or exception text is projected.
  Snapshot disk paths use the canonical path redactor; probe errors map to
  fixed codes; event lines are re-redacted with bounded limits; JSONL roots are
  reduced to safe basename/digest.
- Telemetry is explicitly off (`telemetry_enabled=false`,
  `remote_transport=none`); no remote transport, background thread, poller,
  sampler, DB write, or export exists in the new code.
- Pagination, retention, event lines, filenames, and reasons are hard-bounded;
  invalid cursors are rejected by FastAPI (422) or the canonical 400 envelope;
  unconfigured services return canonical 503 envelopes instead of raw errors.
- Resource and observability services use `RLock` so concurrent lease/snapshot
  and event/sink reads are consistent; no product background thread is
  introduced.

## Residual risk

- Routers are isolated and not yet registered in `main.py`; the serial
  integration delta below is required before the endpoints appear on the app.
- `LocalJsonlSink` construction depends on host directory-fd support; the
  registry models this honestly as `PLATFORM_UNSUPPORTED` when wired that way.
- Registry retention is a bounded in-memory index separate from the accepted
  `BoundedMemorySink` ring buffer; both stay bounded and neither is persistent.
- The four failures in `core/tests/api/test_capability_authoring.py`
  (`TestExplicitDatabaseCommit`) are pre-existing and out of ownership: their
  `UnitOfWork.commit` monkeypatch intercepts `recover_interrupted_pull_jobs`
  inside `create_app`, and they fail identically in isolation on this base.
  They were not repaired because those files are not owned by this task.
- No live API server, browser, app, model, runtime, provider, install, broad
  suite, performance, or load verification ran under the host-safety policy.

## Blockers

None. No INTERFACE contract outside the owned surface was changed.

## Serial main integration delta

In `core/zana_core/main.py` (lead-owned, not edited here):

1. Construct a `ResourceService` over the canonical data root, e.g.
   `ResourceService(provider=DefaultSnapshotProvider(workspace_path=resolved_data_root))`
   and set `app.state.resource_service`.
2. Construct a local `ObservabilityRegistry`, e.g.
   `ObservabilityRegistry(memory_sink=BoundedMemorySink(max_events=200, max_bytes=4*1024*1024), jsonl_sink=LocalJsonlSink(log_root=resolved_data_root / "logs", filename="zana.jsonl", max_bytes=64*1024, max_retention=5))`;
   if `LocalJsonlSink` raises `PlatformUnsupportedError`, construct the registry
   with `jsonl_error="PLATFORM_UNSUPPORTED"` instead. Set
   `app.state.observability_registry`.
3. Include the isolated routers:
   `app.include_router(resources_router)` from
   `zana_core.api.resources` and `app.include_router(observability_router)`
   from `zana_core.api.observability`.
4. Add the new paths to the canonical `AUTH_PROTECTED_PATHS` contract test when
   the routers are registered.

No shared schema, DB, job, desktop, manifest, or lockfile change is required
for this surface.

## Merge instructions

Integrate the single implementation commit `0c31e3c` and this receipt commit
onto the canonical lane at base `52bc1be`. No lockfile, migration, shared API
schema, `main.py`, DB file, desktop source, or GoalBuddy state is included.
After integration, rerun the owned resource/observability/new API suites and
the full Core suite under the lead's loopback policy.

## Accepted commit and clean proof

- Implementation commit: `0c31e3c4dc78bbb832a0ae0216a66fbe7b2a6823`
- Clean proof after product commit: `git status --porcelain` empty.
- Remote: no push attempted; explicit push blocker is lead integration under
  ZANA remote policy. No remote SHA is claimed.

## Correction addendum (lead BLOCK repairs)

Lead review blocked the initial product commit on resource/observability
resource-safety issues. The following were repaired in owned paths only in
one focused correction commit:

- Public token/request leak closed: `ResourceLeaseRead` and `ResourceUsageRead`
  now expose `lease_ref` and `request_id` as stable salted SHA-256 public
  references (`lease-...` / `request-...`); the raw release token and arbitrary
  request id never reach API output. Exact absence tests cover leases, usage,
  and hostile request ids.
- Governor memory bounded at write time: `ResourceGovernor._records` is now a
  `deque` with exact count and serialized-retention-byte caps; `_append_record`
  evicts oldest exactly once and monotonic `history_dropped` /
  `history_serialized_bytes_dropped` counters are never incremented by reads.
  `configure_usage_history` immediately trims an already-populated injected
  governor. Defaults are conservative (256 records / 256 KiB) with hard caps.
- Registry memory bounded by count and bytes: `ObservabilityRegistry` now
  tracks `retained_bytes`, `retention_dropped_bytes`, `max_retained_bytes`, and
  evicts oldest on either bound; defaults are 500 events / 2 MiB with hard
  caps. Multibyte and byte-bound behavior is tested.
- Deterministic lifecycle: idempotent thread-safe `close()` closes the owned
  JSONL sink and reports `closed=true`; writes after close fail with
  `REGISTRY_CLOSED`; retained events and truthful byte/count stats remain
  readable after close; JSONL health becomes `available=false`,
  `reason="CLOSED"` after close. No fd leak.
- Partial delivery truth: `write()` returns `ok=true` with
  `error="PARTIAL_DELIVERY"` when at least one sink accepts and another
  fails; all-failed returns `ALL_SINKS_FAILED`; health reports bounded
  `failures` and `partial_deliveries` counters. Invalid events, serialization
  failure, and `NO_SINKS_CONFIGURED` also increment `failures`.
- Identifier privacy: one exact sanitized `Event` is built before persistence
  and is passed to every sink, serialization, and returned event id. Every
  public identifier field is sanitized (`operation_id`, `job_id`, `phase`,
  `recovery_code`, and all `EventContext` identifiers). Raw path, control,
  credential/token/bearer/secret lookalikes are replaced with a bounded
  `redacted-...` reference; empty optional identifiers stay empty. Tests read
  actual memory sink snapshots and JSONL files to prove raw hostile
  identifiers never persist.
- Thread-safety/config: the public governor escape hatch is removed; callers
  use service wrappers. Passing an exact governor together with
  policy/provider is rejected. `stale_after_seconds` rejects NaN/infinity and
  invalid injected clocks are rejected before side effects.
- Serialized-retention bytes: `_usage_record_bytes` is documented as a
  deterministic non-parseable accounting frame, not compact JSON and not heap
  bytes; API fields are named `history_serialized_bytes` /
  `history_serialized_bytes_dropped` and tested with multibyte content.

### Revised serial integration delta

In `core/zana_core/main.py` (lead-owned, not edited here):

1. Construct a `ResourceService` over the canonical data root, e.g.
   `ResourceService(provider=DefaultSnapshotProvider(workspace_path=resolved_data_root))`
   and set `app.state.resource_service`.
2. Create/validate the logs directory through the accepted platform boundary:
   call `ensure_roots(paths, kinds=(PathRoot.LOG,))` when `platform_paths` is
   available, then construct
   `ObservabilityRegistry(memory_sink=BoundedMemorySink(max_events=200, max_bytes=1*1024*1024), jsonl_sink=LocalJsonlSink(log_root=paths.log_root, filename="zana.jsonl", max_bytes=64*1024, max_retention=5))`.
   If `LocalJsonlSink` raises `PlatformUnsupportedError`, construct the
   registry with `jsonl_error="PLATFORM_UNSUPPORTED"` instead. Set
   `app.state.observability_registry`.
3. Include the isolated routers:
   `app.include_router(resources_router)` from
   `zana_core.api.resources` and `app.include_router(observability_router)`
   from `zana_core.api.observability`.
4. Close the registry deterministically in `lifespan` shutdown before the
   database cleanup: `observability_registry.close()` (idempotent). After
   close, health reports `closed=true` and JSONL `available=false`.
5. Add the new paths to the canonical `AUTH_PROTECTED_PATHS` contract test when
   the routers are registered.

### Correction gates

- New focused tests: 47 passed (resource service, observability registry,
  resource/observability API).
- Owned suites: 247 passed.
- Ruff check and format: clean.
- Pyright owned implementation: 0 errors, 0 warnings.
- Import smoke: pass.
- `git diff --check`: pass.
- Clean proof after correction commit: `git status --porcelain` empty.
- Correction commit: `4f04fc1814556dea984c44c0a9fefdb98bca236c`
- Prior implementation commit preserved: `0c31e3c4dc78bbb832a0ae0216a66fbe7b2a6823`

## Lead acceptance

- Verdict: PASS
- Canonical integration commits: `287a6c8`, `a685567`, `b48c694`, `295fc21`
- Independent canonical gates: 247 focused tests PASS; Ruff check/format PASS;
  Pyright 0 errors/warnings; direct bounds, redaction, failure-path, close,
  retention and telemetry-off review PASS; `git diff --check` PASS.
- Security delta: no raw lease token, arbitrary request id, host path, secret or
  raw exception crosses the new API; sanitized events are the only events sent
  to sinks; write-time count/byte bounds prevent unbounded histories.
- Residual risk: isolated routers and service lifecycle are not yet registered
  in `main.py`; that shared change remains reserved for the serial integration
  milestone. Broad/live/model/runtime/app/browser tests remain intentionally
  deferred under the host-safety policy.
- Source worktree clean proof: `git status --porcelain` empty at `4c94c71`.
- Canonical acceptance commit: `f1cfebde49af0d2ccf949f08044e2450c7de52af`.
- Canonical remote proof: non-force push succeeded and `git ls-remote`
  confirmed `refs/heads/main` exactly at the acceptance commit before this
  final receipt update.
- Canonical clean proof: index and worktree were empty after the acceptance
  commit and remote verification.
