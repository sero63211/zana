# T007-planning Handoff - Deterministic Non-Executing Build Planner

Verdict: PASS

## Scope

Implemented the pure deterministic build-planning lane for the low-memory
architecture wave, starting exactly from integrated commit `57a086a`. Only
owned paths were touched:

- `core/zana_core/planning/**`
- `core/tests/planning/**`
- `.agent-work/handoffs/T007-planning.md`

No subprocess, model, download, training, inference, embedding, artifact
write, DB/API wiring, or runtime materialization occurs anywhere in the
implementation or tests. Only existing dependencies are used.

## Changed files and modules

- `core/zana_core/planning/models.py` - immutable typed inputs and outputs:
  `BuildPolicy` (auto/explicit strategy, offline/ask/deny-after-acquisition
  behavior, `prefer_training`, `max_disk_gb`, `max_memory_fraction`,
  `require_verification`, adapter allowance, external download approval,
  safety reserve) with fail-closed contradiction validation; `ModelFacts`
  with `runtime_identity`/`training_source_identity`/`adapter_base_identity`
  and exact identity strength; `CapabilityFacts` and `EvaluationFacts` with
  canonical converters from the T007-capabilities validation result;
  `TrainingProviderCompatibility`; `HardwareFacts` with a converter from the
  T007-hardware profile; `StrategyDecision`; `ApprovalRequirement`/
  `ApprovalSet`; `ResourceEstimate`/`ResourceCheck`;
  `CancellationCheckpoint`/`PhasePlan`/`LifecyclePlan`; and `BuildPlan` with
  a stable SHA-256 digest over canonical sorted JSON.
- `core/zana_core/planning/strategy.py` - deterministic composition: RAG for
  factual/updatable/citation/large/scarce-example knowledge; trusted
  built-in tools only (`zana.calculator` canonical set mirrored from the
  permissions lane); adapter only for supported task-oriented goals with
  sufficient supervised examples, disjoint held-out evaluation, exact digest
  identity, provider/runtime/hardware compatibility, and policy allowance.
  Book/document corpora without supervised examples never train. Explicit
  strategy overrides return structured `STRATEGY_INCOMPATIBLE` blockers
  instead of silently falling back.
- `core/zana_core/planning/estimates.py` - conservative disk/memory min/max
  ranges with explicit assumption strings, documented multipliers, a safety
  reserve, honest unknown state (`None` when sizes are unknown), and a
  `duration_estimate` that is always `unknown` (never promises timing).
- `core/zana_core/planning/lifecycle.py` - canonical build lifecycle phase
  ordering (`ANALYZING`, `BASELINE_RUNNING`, `PLANNED`, optional
  `ACQUIRING_APPROVED_ARTIFACTS`/`BUILDING_KNOWLEDGE`/`TRAINING_ADAPTER`/
  `MATERIALIZING`, `EVALUATING`, `PACKING`, conditional `VERIFIED`) with
  per-phase cancellation-checkpoint metadata (safe cancellation, subprocess
  termination, transaction rollback, temp cleanup).
- `core/zana_core/planning/planner.py` - `BuildPlanner.plan(...)` combining
  model compatibility gates (runtime online, context minimum, required model
  capabilities), verification-requires-evaluation, strategy, resources,
  approvals (downloads, training, permissions, disk), lifecycle, aggregated
  blockers/warnings, `approvable`, and the digest.
- `core/tests/planning/**` - 62 focused tests covering RAG/tools/adapter
  composition, book/no-training rule, exact identity, held-out leakage
  signal, provider/runtime/hardware incompatibility, disk/memory thresholds,
  approvals and offline behavior, deterministic digest, overrides, policy
  validation, lifecycle ordering, immutability, and fact converters.

## Checks run and evidence

All commands used the existing shared environment at
`/Users/sero/Documents/zana/core/.venv/bin`; no dependencies were installed
and no venv was created.

| Check | Command | Result |
| --- | --- | --- |
| Focused planning tests | `core/.venv/bin/python -m pytest core/tests/planning -q` | 62 passed |
| Full Core suite | `core/.venv/bin/python -m pytest core/tests -q` (escalated for loopback/network) | 322 passed, 1 pre-existing environment-sensitive hardware test failed (see residual risk) |
| Ruff lint | `core/.venv/bin/ruff check core/zana_core/planning core/tests/planning` | clean |
| Ruff format | `core/.venv/bin/ruff format --check core/zana_core/planning core/tests/planning` | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core/planning` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

## Security delta

- The planner is data-only and never executes capability content or external
  processes; no hooks, shell, Python, or subprocess execution paths exist.
- Exact identity rules prevent training on unproven or display-name-inferred
  base models; missing/weak identity blocks adapter selection.
- Held-out leakage signal is required for adapter training; explicit adapter
  overrides with leakage are hard blockers.
- Offline/deny modes fail closed: adapter checkpoint downloads under
  `offline` acquisition or `offline` download policy are blockers, not
  silent fallbacks.
- Resource limits are enforced conservatively with a safety reserve; unknown
  sizes are reported honestly rather than claimed safe.
- Unknown policy fields and contradictory settings are rejected by the
  frozen `BuildPolicy` model (`extra="forbid"`).

## Residual risk

- The full Core suite's `test_darwin_metal_real_probe` (owned by
  T007-hardware) fails when run after other provider tests on this host
  (`platform_accelerators` returned 0 accelerators) but passes when run
  alone. It is environment-sensitive, unrelated to this lane, and was not
  modified; the PM integration lane should rerun it on the integrated branch.
- The loopback transport tests require network permission (sandboxed runs
  report them as errors); the escalated full-suite run above is the
  canonical evidence.
- Scorer/training-goal semantics are V1 canonical sets; extending them
  requires coordinated changes in capabilities/planning/training lanes.
- The planner intentionally has no API/DB wiring; the integration lane owns
  exposing plans through the authenticated API and persisting approved plans.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `2cb29a6` (`feat: add deterministic build planner`)
  on branch `agent/T007-planning`, started exactly at integrated commit
  `57a086a`.
- This handoff is committed separately on the same branch.
- Cherry-pick both commits onto the integration lane. No lockfiles, DB
  schema, API registration, desktop files, or GoalBuddy state are included.
  After integration, rerun the focused planning suite and the full Core
  suite with loopback permission.
