# T007 Diagnostics Handoff — Bounded Read-Only Doctor Services

Verdict: PASS

## Scope

Implemented bounded diagnostic/doctor services under
`core/zana_core/diagnostics/**` and focused tests under
`core/tests/diagnostics/**`. No API/DB/runtime/hardware/shared file was edited,
no dependency was added, no model/runtime was started, and no recursive store
scan or background work was performed.

## Changed files and modules

- `core/zana_core/diagnostics/models.py` — immutable `DiagnosticCheck`,
  `DiagnosticIssue`, `DiagnosticReport`, `Severity`, `FeatureReadiness`,
  `RecoveryAction`, `ProbeBudget`, redacted `Evidence`, and deterministic
  `AggregateHealth`.
- `core/zana_core/diagnostics/probes.py` — cheap default probes: platform,
  memory/disk headroom, SQLite reachability via injected read-only checker,
  storage roots metadata-only, runtime discovery through the bounded existing
  registry, optional dependency metadata via `importlib.metadata`, and
  loopback/auth presence without token exposure.
- `core/zana_core/diagnostics/doctor.py` — deterministic sequential
  `DoctorService` enforcing max check count, per-check timeout contract, total
  time budget, and probe-exception isolation; no threads, daemons, polling, or
  telemetry.
- `core/tests/diagnostics/**` — 22 focused tests.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused pytest | `core/.venv/bin/python -m pytest core/tests/diagnostics -q` | 22 passed |
| Ruff lint | `core/.venv/bin/ruff check core` | clean |
| Ruff format | `core/.venv/bin/ruff format --check core` | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

Test coverage includes low memory/disk, missing optional packages, runtime
absent/present, DB WAL/FK failure, storage permissions, time/check/output
budgets, probe exceptions/timeouts, Windows/Linux/macOS routing, secret/path
redaction, narrow feature readiness, and aggregate health.

## Security and lightweight delta

- Diagnostics are read-only by default: no recursive artifact scan/hash, no
  model/runtime start, no admin assumption, no macOS-only commands on other
  platforms, no daemon/polling/telemetry.
- Evidence is redacted: basename/hash/boolean presence only; full private
  paths, document names, environment dumps, and bearer token values are never
  exposed.
- Recovery actions are safe and actionable data only; nothing auto-installs,
  starts, downloads, or deletes.
- Optional feature absence never blocks Core startup; aggregate health fails
  only for mandatory integrity/auth/storage failures.

## Residual risk

- The SQLite probe depends on an injected read-only checker contract; the
  live FastAPI wiring of that checker and the full `/system/doctor` route
  remain for an integration/API lane.
- Runtime discovery uses the existing bounded registry, which performs local
  endpoint probes; no model is loaded or started, but a later integration
  should respect the report's per-check timeout contract when wiring it.
- Live low-resource and real Windows/Linux behavior are covered by injected
  boundaries; full platform smoke remains for integration.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `0dd9676`
  (`feat: add bounded diagnostic and doctor services`) on branch
  `agent/T007-diagnostics`, started exactly at base commit `044c489`.
- This handoff is committed separately on the same branch.
- Merge `core/zana_core/diagnostics/**`, `core/tests/diagnostics/**`, and this
  handoff through the PM integration lane. No lockfile, manifest, API, DB,
  runtime, hardware, or other lane is included.
