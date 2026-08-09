# T007 Instances Handoff — Fail-Closed Instance and Injected Chat Orchestration

Verdict: PASS

## Scope

Implemented the T007 instances lane under `core/zana_core/instances/**` and
`core/tests/instances/**`, building on integrated runtime, image, memory,
knowledge, and permission contracts. No DB/API wiring, dependency addition,
runtime/model startup, inference, or shared contract change was introduced.

## Changed files and modules

- `core/zana_core/instances/models.py` — strict immutable `InstanceConfig`,
  `StartPlan`, `SessionBinding`, `ResponseProvenance`, `ChatInput/Output`,
  `LowResourceLimits`, tool/provenance records; mutable pointer/state stays in
  the integrated memory models.
- `core/zana_core/instances/creation.py` — fail-closed creation binding
  immutable image config/digest and separate mutable pointer/state; rejects
  not-runnable images, weak/missing exact base identity, unresolved required
  artifacts, and unresolved secret requirements.
- `core/zana_core/instances/runtime_selection.py` — exact model identity
  selection; never matches display name; runtime disappearance/drift,
  incompatible runtime, missing capabilities, and weak identity all block
  start with typed errors.
- `core/zana_core/instances/lifecycle.py` — start/stop/session state machine
  with injected `RuntimeSessionAdapter`, optimistic revision checks,
  idempotent transitions, exact binding verification, and clean error state.
- `core/zana_core/instances/chat.py` — injected `RetrievalAdapter`,
  `InferenceAdapter`, and `ToolExecutor` orchestration with protected context,
  default-deny tool gating before execution, pending memory proposals,
  complete provenance, honest partial/failure/cancel/timeout results, and
  bounded low-resource limits.
- `core/zana_core/instances/errors.py` — typed error records with explicit
  recovery actions.
- `core/tests/instances/**` — 44 focused tests.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Instances tests | `pytest core/tests/instances -q` | 44 passed |
| Full Core suite | `pytest core/tests -q` | 470 passed, 1 unrelated hardware Metal probe failure |
| Ruff lint | `ruff check core` | clean |
| Ruff format | `ruff format --check core` | clean |
| Pyright | `pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

The one full-suite failure is `core/tests/hardware/test_providers.py::test_darwin_metal_real_probe`
from another lane (host Metal probe), not touched by this lane.

## Low-resource enforcement

Zero new dependencies. `LowResourceLimits` bounds message/instruction chars,
retrieval chunks/text, memory records, conversation turns, tool requests and
argument chars, memory suggestions, context chars, and generation timeout.
Retrieval is bounded after the adapter call, context is composed once under
the token budget, no unbounded threads/tasks are created, and all failures
return typed `ChatError`/`InstanceErrorRecord` recovery instead of crashing.

## Security delta

- Tool execution is gated through the integrated default-deny
  `PermissionDecisionEngine`; unknown/denied/oversize tools are never
  executed and yield structured denial records.
- RAG evidence is delimited untrusted data; permissions are enforced in code.
- Only approved memory is included; model suggestions become pending
  proposals unless an explicit category auto-memory policy allows them.
- Exact image/base-model/runtime/session identities are verified on start and
  chat; display-name matching and silent substitution are impossible.
- No secrets, tokens, or mutable image state are written by this lane.

## Residual risk

- Retrieval/inference/tool adapters are injected protocols only; live runtime
  wiring and DB/API persistence remain for integration lanes.
- `estimate_tokens` remains the integrated deterministic text approximation;
  exact model tokenizers can inject precise `ContextItem.tokens`.
- The full-suite hardware Metal probe failure is outside this lane and was
  not modified.

## Blockers

None.

## Commit and cherry-pick instructions

- Implementation commit: `aec547d`
  (`feat: add fail-closed instance and injected chat orchestration`) on
  branch `agent/T007-instances`, started exactly from integrated commit
  `79629d5`.
- This handoff is committed separately on the same branch.
- Cherry-pick `aec547d` and the handoff commit onto `master` through the PM
  integration lane. Only `core/zana_core/instances/**`, `core/tests/instances/**`,
  and this handoff are included; no lockfile or shared manifest changed.
