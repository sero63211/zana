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
| Focused API suite (`core/tests/api`) | 67 passed |
| New system/runtime/model tests (`test_system_runtime_models.py`) | 9 passed |
| Platform main integration (`test_main_integration.py`) | 7 passed |
| Platform/runtimes/jobs/acquisition suites | 281 passed |
| Full Core suite (`core/tests`) | 1598 passed |
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

- `/system/doctor` intentionally wires the deterministic read-only probes and
  excludes `RuntimeDiscoveryProbe` and `StorageRootProbe`, which require injected
  transports or path wiring this API boundary does not own; the report honestly
  reflects the probes that are wired.
- `/runtimes/refresh` does not prune stale model/runtime rows after discovery;
  it upserts only, which is conservative and avoids deleting other owners' data.
- Remote manual runtime reachability is recorded but not probed because the
  canonical registry enforces loopback-only probing.
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
- Receipt commit: this handoff commit (resolve with `git rev-parse HEAD`).
- Branch: `agent/t900-api-system-runtime`
- Remote: none; no push attempted (explicit push blocker is lead integration).
- Clean proof after all commits: `git status --porcelain` empty and
  `git diff --check` pass; verified with index and worktree diffs both clean.
