# T007-resources Handoff - Strict Lightweight Resource Admission Governor

Verdict: PASS

## Scope

Implemented the dependency-free, zero-background-thread resource admission
governor lane for the lightweight-architecture wave, starting exactly from
integrated commit `13af8c6`. Only owned paths were touched:

- `core/zana_core/resources/**`
- `core/tests/resources/**`
- `.agent-work/handoffs/T007-resources.md`

No model/runtime/inference/training/embedding/download, no package install,
no uv sync/venv, no native build, and no background process/thread, sleep, or
poll loop exists anywhere in this lane. ZANA stays strictly lightweight on
low-resource hosts: features are admitted conservatively or fail explicitly,
never by guess.

## Changed files and modules

- `core/zana_core/resources/models.py` - immutable typed models with
  `extra="forbid"` and bounded non-negative validation: `ResourceSnapshot`
  (platform label, os/arch, cores, total/available memory, workspace disk,
  probe error, notes), `ResourcePolicy` (memory reserve, disk reserve,
  safety fraction, disk overhead fraction, open-file/recursion caps,
  adaptive heavy concurrency, per-category `CategoryLimit`), `OperationRequest`
  (category, memory/disk/workers/items/bytes/open files/recursion/TTL),
  `AdmissionDecision` with `DenialReason`/`RecoveryAction`, `ResourceLease`
  bound to request/policy/snapshot revision, and `UsageRecord`.
  `ResourcePolicy` auto-completes every category (tiny/metadata/read-only
  bounded cheap categories plus build/embedding-index/inference/training/
  export/portability heavy categories with distinct caps) and rejects
  mismatched category limits.
- `core/zana_core/resources/snapshot.py` - `SnapshotProvider` protocol plus
  `DefaultSnapshotProvider` using only `psutil`, `shutil`, and `platform`.
  Every probe failure is non-fatal and surfaces as unknown fields plus a
  `probe_error`; no fake zero/success values are ever fabricated.
- `core/zana_core/resources/governor.py` - synchronous deterministic
  `ResourceGovernor`: `admit` accounts for all active leases before deciding,
  enforces memory/disk budgets (reserve + safety + disk overhead for temp,
  final, rollback/cleanup), category concurrency/worker/item/byte/file/
  recursion caps, adaptive heavy concurrency (1 on constrained/unknown hosts,
  bounded small cap on large hosts), and returns ASK/BLOCK for unknown sizes
  or headroom. `release`/`cancel` restore accounting immediately; double
  release and stale tokens raise `ResourceLeaseError`; `reap_expired` runs
  only on explicit calls with an injected clock; `lease()` context manager
  always releases on success and exception; `refresh()` increments the
  snapshot revision.
- `core/zana_core/resources/batching.py` - bounded `BatchPlan`,
  `validate_batch_limits`, and `iter_batches` generator that yields
  item/byte-capped batches without materializing the input, never copies
  large byte buffers, and raises `BatchLimitError` before unbounded growth
  when a single item exceeds `max_bytes` or total items exceed a cap.
- `core/zana_core/resources/guards.py` - pure integration guard protocols for
  build, embedding/index, inference/training, and portability services; these
  modules are not imported or invoked.
- `core/tests/resources/**` - 58 focused tests across simulated 4/8/16/32 GB,
  unknown memory, low disk, Windows/Linux/macOS labels, concurrency
  contention, lease lifecycle/expiry/double-release, overflow/invalid
  requests, safety reserve, category caps, streaming batch bounds with
  generators, cancellation/release, provider probe failure, and
  deterministic decisions.

## Checks run and evidence

All commands used the existing shared environment at
`/Users/sero/Documents/zana/core/.venv/bin`; no dependencies were installed
and no venv was created.

| Check | Command | Result |
| --- | --- | --- |
| Focused resource tests | `core/.venv/bin/python -m pytest core/tests/resources -q` | 58 passed |
| Full Core suite | `core/.venv/bin/python -m pytest core/tests -q` (escalated for loopback/network) | 619 passed |
| Ruff lint | `core/.venv/bin/ruff check core/zana_core/resources core/tests/resources` | clean |
| Ruff format | `core/.venv/bin/ruff format --check core/zana_core/resources core/tests/resources` | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core/resources` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

## Lightweight and security delta

- Zero background threads, timers, polling loops, daemons, telemetry, or
  admin/GPU commands; the only process work is a cheap synchronous snapshot
  via `psutil`/`shutil`/`platform` at explicit calls.
- Probe failures surface as unknown fields plus a probe error; heavy
  operations on unknown memory/disk are ASK/BLOCK, never allowed by guess.
- Memory is never allocated against total RAM alone: OS/application reserve
  and a safety fraction are always deducted; disk includes reserve plus
  temp/final/rollback overhead.
- Unknown policy fields, contradictory settings, negative values, and
  values above the safe byte bound are rejected by the frozen models.
- Lease tokens bind the exact request, policy revision, and snapshot
  revision; double release and stale tokens fail cleanly; cancellation
  restores reservation accounting immediately.

## Residual risk

- The governor is deliberately not wired to the API/DB; the integration lane
  owns exposing decisions and leases through the authenticated API and job
  lifecycle.
- Per-category defaults are V1 canonical; tuning them requires coordinated
  changes in the resources lane and any consuming integration.
- `DefaultSnapshotProvider` uses `psutil` from the existing frozen dependency
  set; on hosts where psutil reports unavailable values they stay `None`
  rather than being invented.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `f0bea3e` (`feat: add strict resource admission governor`)
  on branch `agent/T007-resources`, started exactly at integrated commit
  `13af8c6`.
- This handoff is committed separately on the same branch.
- Cherry-pick both commits onto the integration lane. No lockfiles, DB
  schema, API registration, desktop files, or GoalBuddy state are included.
  After integration, rerun the focused resource suite and the full Core
  suite with loopback permission.
