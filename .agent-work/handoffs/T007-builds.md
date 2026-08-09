# T007 Builds Handoff — Pure Persistent-Safe Build Lifecycle Foundation

Verdict: PASS

## Scope

Implemented the pure build lifecycle foundation under
`core/zana_core/builds/**` and its focused tests under
`core/tests/builds/**`. No DB/API wiring, no phase execution, no subprocess,
model, runtime, embedding, training, archive, or external side effect was
invoked. No frozen domain/job/API/DB/shared contract was changed.

## Changed files and modules

- `core/zana_core/builds/models.py` — immutable typed lifecycle record, phase
  attempt, checkpoint, approval requirement, progress, failure, recovery,
  cleanup, cancellation request/acknowledgement, finalization, and build-plan
  models.
- `core/zana_core/builds/state_machine.py` — exact fail-closed transition
  graph from DRAFT through VERIFIED with BLOCKED/FAILED/CANCELLED/
  VERIFICATION_FAILED rules; optional TRAINING_ADAPTER is explicitly skippable.
- `core/zana_core/builds/service.py` — transition service with optimistic
  concurrency via expected revision, immutable history revisions, truthful
  progress, explicit checkpoints, failure recording, cancellation request/
  acknowledgement, recovery planning, and stale runtime/model drift blocking.
- `core/zana_core/builds/approvals.py` — typed approval gates for downloads,
  training, permissions, and disk estimate with plan digest binding, expiry,
  offline fail-closed behavior, and phase-start enforcement.
- `core/zana_core/builds/workspaces.py` — `data/jobs/<validated-job-id>`
  derivation under an injected approved data root with traversal/symlink-safe
  job id validation and data-only cleanup plans.
- `core/zana_core/builds/runners.py` — pure phase-runner/progress/event
  protocols for later integration; nothing is executed by this lane.
- `core/tests/builds/**` — 28 focused tests.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused pytest | `core/.venv/bin/python -m pytest core/tests/builds -q` | 28 passed |
| Ruff lint | `core/.venv/bin/ruff check core` | clean |
| Ruff format | `core/.venv/bin/ruff format --check core` | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

Test coverage includes every valid/invalid transition, optional training
skip/use, approval grants and invalidation, offline/denied fail-closed,
optimistic concurrency, cancellation and acknowledgement, retry/resume,
partial-artifact rejection, runtime/model drift blocking, workspace traversal
and symlink cases, finalization order, nondestructive rebuild history, and
honest progress.

## Security delta

- All lifecycle and workspace logic is data-only; no path outside the
  injected approved data root can be derived, and job ids reject traversal.
- Partial artifacts are always marked unusable on failure/cancellation and
  can never be promoted.
- Approval changes invalidate prior grants; offline mode denies downloads and
  training before any acquisition or training phase can start.
- External side effects are never claimed rolled back; finalization keeps
  verify-digests-first, atomic-move intent, then transactional image-registration
  intent as explicit data.

## Residual risk

- This lane provides contracts and pure services only; real phase runners,
  subprocess termination, digest verification, atomic moves, and image
  registration must be wired by later integration/build-execution lanes.
- Recovery currently requires a BLOCKED state with explicit resumable
  checkpoints; no automatic retry policy exists yet by design.
- Pydantic frozen models copy history into new records, which is intentional
  for immutable revisioning but should be persisted by the later DB lane.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `4b4ddf3`
  (`feat: add pure build lifecycle foundation`) on branch
  `agent/T007-builds`, started exactly at base commit `8a04b73`.
- This handoff is committed separately on the same branch.
- Merge `core/zana_core/builds/**`, `core/tests/builds/**`, and this handoff
  through the PM integration lane. No lockfile, manifest, DB, API, or other
  lane is included.
