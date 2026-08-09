# T900 API System/Runtime/Model Handoff

Verdict: PASS

## Scope

Implemented the authenticated typed system/runtime/model API vertical over the
canonical services, starting exactly from integrated base
`9c1dfb4a2ee4b30fe836d795ef4663e3d921bd75` on isolated branch
`agent/t900-api-system-runtime`.

Only the exclusive owned files were touched:

- `core/zana_core/api/doctor.py`
- `core/zana_core/api/runtimes.py`
- `core/zana_core/api/models.py`
- `core/zana_core/api/system.py`
- `core/zana_core/api/schemas.py`
- `core/zana_core/api/deps.py`
- `core/zana_core/main.py`
- `core/tests/api/test_api_contracts.py`
- `core/tests/api/test_system_runtime_models.py`
- `core/tests/platform/test_main_integration.py`
- `.agent-work/handoffs/T900-api-system-runtime.md`

No other task's file, migration, manifest, lockfile, desktop source, or shared
contract was modified. No dependency install, network request, runtime/model
start, model download, inference, training, desktop launch, or native build was
performed. Protocol fixtures are used only in tests.

## Changed modules and behavior

- `core/zana_core/api/system.py` (new): `GET /api/v1/system/profile` returns a
  real bounded `HardwareProfile` via `collect_profile` over the Core data root;
  `GET /api/v1/system/doctor` returns a bounded `DiagnosticReport` built by
  `DoctorService` from deterministic read-only probes (platform, memory/disk,
  SQLite reachability, optional dependency metadata, loopback/auth). Both are
  loopback-authenticated with the canonical error envelope.
- `core/zana_core/api/doctor.py`: converted to a backward-compatible re-export
  of the system router, so the historical `zana_core.api.doctor` import path
  keeps working while the concrete doctor route deliberately moved to
  `/api/v1/system/doctor` returning the diagnostic report.
- `core/zana_core/api/runtimes.py`: added `POST /api/v1/runtimes/refresh`,
  which records a persisted `runtime_refresh` job, runs bounded registry
  discovery (default loopback targets plus manual loopback runtimes), upserts
  discovered runtimes/models into the DB, and returns the job. Existing
  `GET /runtimes`, `POST /runtimes/manual`, and `DELETE /runtimes/{id}`
  behavior is preserved unchanged.
- `core/zana_core/api/models.py`: added `POST /api/v1/models/pull`, which
  validates the runtime is Ollama, builds a typed `NativeAcquisitionPlan`, and
  persists a `model_pull` job with the bounded plan; it never proxies bytes and
  never starts a pull. Existing models list/detail are preserved.
- `core/zana_core/main.py`: accepts an optional injected `RuntimeProbeRegistry`
  (stored on `app.state.runtime_registry` for test isolation), registers the
  `system` router, and drops the superseded separate doctor registration.
- `core/zana_core/api/schemas.py`: added strict `ModelPullCreate` request model
  with exact bounds; `SystemDoctorRead` is retained for backward compatibility.

## Checks run and evidence

All commands used the existing shared venv
`/Users/sero/Documents/zana/core/.venv/bin`; no dependencies were installed and
no venv was created.

| Check | Result |
| --- | --- |
| Focused API suite (`core/tests/api`) | 78 passed |
| Focused db suite (`core/tests/db`) | 11 passed |
| Platform suite (`core/tests/platform`) | 53 passed |
| API/db/runtime/diagnostic/platform/jobs/acquisition suites | 392 passed |
| Full Core suite (`core/tests`) | 1611 passed |
| Ruff check (owned files) | PASS |
| Ruff format check (owned files) | PASS |
| Pyright (owned implementation modules) | 0 errors, 0 warnings |
| `git diff --check` | PASS |

## Security delta

- Every new endpoint requires the exact per-launch bearer token; missing/wrong
  tokens return the canonical `401` envelope.
- Discovery refresh probes only loopback candidates; remote manual endpoints are
  persistently recorded but never probed (registry is loopback-only), and
  failures are sanitized into a generic canonical error with recovery actions.
- Model pull validates the runtime kind and bounded model reference before
  persisting any request; model bytes are never proxied and no pull is started.
  The persisted plan is bounded and contains no secrets (endpoints are loopback
  or validated manual entries, and manual entries reject embedded credentials).
- Diagnostic and hardware endpoints are read-only and bounded; evidence is
  redacted by the existing diagnostic service and no raw exceptions or private
  paths leave the API.

## Residual risk

- `/runtimes/refresh` prunes stale models only for online, registered runtimes;
  stale models for offline/failed/unregistered descriptors are retained
  conservatively.
- Remote manual runtime reachability is recorded but not probed because the
  canonical registry enforces loopback-only probing.
- The doctor's runtime probe uses the injected registry; production performs
  bounded loopback discovery only, never external network or model starts.
- No live runtime/model/network operation was exercised; verification used the
  injected fake registry and protocol fixtures only.

## Blockers

None.

## Merge instructions

Integrate the single implementation commit and this handoff commit separately
onto the canonical lane at base `9c1dfb4a`. No lockfile, migration, API
registration outside `main.py`, desktop file, or GoalBuddy state is included.
After integration, rerun the focused API/platform suites plus the full Core
suite with loopback permission.

## Accepted commits and clean proof

- Implementation commit: `9176532`
- Review-fix commit: `48b89cb`
- Receipt commit: this handoff commit (resolve with `git rev-parse HEAD`).
- Branch: `agent/t900-api-system-runtime`
- Remote: none; no push attempted (explicit push blocker is lead integration).
- Clean proof after all commits: `git status --porcelain` empty and
  `git diff --check` pass; verified with index and worktree diffs both clean.

## Review-fix addendum

Lead review `BLOCK` was repaired with one focused product commit `48b89cb`
(no history rewrite):

1. `/system/doctor` now covers runtime, storage, and training diagnostics:
   resolved data root is stored on `app.state` for production and injected DB
   paths; `RuntimeDiscoveryProbe(app.state.runtime_registry)` and
   `StorageRootProbe(data_root/artifacts, data_root/images)` are wired alongside
   the existing optional-dependency training readiness probes. Tests assert the
   required check ids and use the injected fake registry only.
2. Runtime persistence identity now uses exact `(kind, endpoint, source)` via a
   bounded `RuntimeRepository.get_by_kind_endpoint`; llama.cpp and MLX candidates
   sharing `127.0.0.1:8080` remain separate across refreshes. Manual duplicate
   behavior is unchanged.
3. Online registered discovery is authoritative: models that disappeared from an
   online runtime are pruned; offline/failed/unregistered descriptors never
   prune. Tests cover both directions.
4. Failed refresh persists and returns a `FAILED` job with canonical
   error/action data. Discovery sync runs inside a session savepoint, so partial
   runtime/model writes roll back while the failed job/event commits; the job
   remains fetchable from `/jobs/{id}` and no partial discovery persists.
5. Model pull now requires explicit `user_approved=true`, uses a strict
   extra-forbid `ModelPullCreate`, validates/normalizes the runtime endpoint
   through the canonical local-only acquisition validator, and persists the
   complete bounded `NativeAcquisitionRequest` plus plan. Approval, coercion,
   extra-field, remote-endpoint, and path-endpoint tests were added.
6. Model detail routing supports full path keys via `/{model_key:path}` without
   shadowing the static `POST /pull`; retrieval through a `/`-containing key is
   tested.

Review-fix verification: 45 focused API/db/platform tests, 392 related
API/db/runtime/diagnostic/platform/jobs/acquisition tests, 1611 full Core tests,
Ruff check/format PASS, Pyright 0 errors/warnings, `git diff --check` PASS.
