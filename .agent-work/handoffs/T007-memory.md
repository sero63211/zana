# Handoff T007-memory - Memory State, Reset, and Instance Update Services

Verdict: PASS

## Scope

Implemented the pure low-memory services and protocols for ZANA instance
memory from the authoritative specs `14_RUNTIME_EXECUTION_AND_CHAT.md`,
`15_MEMORY_STATE_AND_VERSIONING.md`, `16_SECURITY_AND_PERMISSIONS.md`,
`21_REPOSITORY_STRUCTURE.md`, `22_TESTING_AND_QA.md`, and
`25_ACCEPTANCE_CRITERIA.md` (read from the corrected
`ZANA_BUILD_PLAN_DETAILED/` path). No DB schema, API contract, runtime
inference, immutable image content, or shared manifest was changed.

## Changed files and touched modules

Implementation (`core/zana_core/memory/**`):

- `models.py` - typed conversation turn, memory proposal/record, approval
  provenance, status, immutable image pointer, mutable instance pointer,
  mutable instance state, and migration-boundary snapshot models
- `approval.py` - explicit approve/reject workflow plus a narrow
  category-gated auto-memory policy; unapproved proposals never become active
  memory
- `context.py` - deterministic context selection under an explicit token
  budget with protected system/permission constraints, explicit memory and
  evidence priorities, and recorded truncation decisions
- `reset.py` - confirmation-token-gated destructive reset covering chat,
  approved memory, and full mutable state with auditable plans/results
- `instance.py` - snapshot/update/rollback orchestration with compatibility,
  migration, and smoke hooks; atomic image-pointer switch only after all pass;
  rollback restores the prior pointer/snapshot and never claims to undo
  external side effects
- `export.py` - versioned instance export/import envelope that can only carry
  unresolved secret requirements and never serializes secret values
- `__init__.py` - package exports

Tests (`core/tests/memory/**`): 55 focused tests covering approval/rejection,
auto-memory category policy, deterministic truncation, protected-constraint
preservation, every reset scope and confirmation failure, update PASS, rollback
on migration/smoke failure, snapshot retention, and secret exclusion.

Handoff: `.agent-work/handoffs/T007-memory.md` (this file).

## Interface facts

- Proposal status maps to the shared T005 `MemoryStatus` contract
  (`PENDING`/`APPROVED`/`REJECTED`); only approved proposals are returned as
  active memory.
- Auto-approval requires an explicit `MemoryAutoPolicy` listing enabled
  categories; `AUTO_MEMORY_POLICY` without an enabled category raises
  `AutoMemoryNotEnabledError`.
- Context selection uses the spec section order, never truncates protected
  sections (system/permission constraints by default), and raises
  `ContextBudgetError` instead of silently dropping protected content.
- Destructive resets require a token derived from instance id, scope, and
  state revision; stale or mismatched tokens raise
  `ResetConfirmationError`. Audit entries record cleared counts, revision, and
  a confirmation fingerprint.
- Instance update requires all three check kinds in order
  (compatibility, migration, smoke); any failure keeps the old image pointer
  and captures no snapshot. Successful updates retain the pre-update snapshot;
  rollback restores it, keeps every snapshot, and sets
  `external_side_effects_not_reverted: true`.
- Export/import schema version is `1`; imports reject unsupported versions and
  any `contains_secret_values` payload. Secret references are always
  unresolved requirements with no value fields in the schema.

## Checks run and evidence

All commands used the existing shared environment at
`/Users/sero/Documents/zana/core/.venv/bin`; nothing was installed or synced.

| Check | Command | Result |
| --- | --- | --- |
| Focused tests | `python -m pytest core/tests/memory -q` | 55 passed |
| Ruff lint | `ruff check core/zana_core/memory core/tests/memory` | clean |
| Ruff format | `ruff format --check core/zana_core/memory core/tests/memory` | clean |
| Pyright | `pyright core/zana_core/memory` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

The full Core suite reports 237 passed and 4 errors, all four errors in
`core/tests/runtimes/test_transport.py` from another lane failing at fixture
setup with `PermissionError: Operation not permitted` while binding a local
socket in this sandbox. That file was not touched by this lane and the failure
is a sandbox network restriction, not a regression from the memory package.

## Security delta

- No new API surface, database access, filesystem access, subprocesses, or
  runtime starts; the package is pure Python and memory-only.
- Export/import models structurally exclude secret values and reject resolved
  requirements or `contains_secret_values` payloads; a defensive scanner
  confirms no sensitive key carries a string value.
- Reset confirmation uses `hmac.compare_digest` over a SHA-256 token derived
  from the exact state precondition; audit stores only a token fingerprint.

## Residual risk

- The module is service/protocol-level only; persistence, API wiring, and
  runtime chat integration are owned by later lanes.
- `estimate_tokens` is a deterministic protocol approximation (one token per
  four characters); exact model tokenizers can supply `ContextItem.tokens`
  when wired into chat.
- Live update/rollback behavior against a real runtime and real image blobs
  remains for integration; the check hooks here are injected boundaries.

## Blockers

None for this lane. The four unrelated runtimes transport errors are a
sandbox socket-bind restriction and should be re-run by the PM in an
unrestricted context.

## Commit and cherry-pick instructions

- Implementation commit: `adff0a3` (`feat: add memory state, reset, and
  instance update services`) on branch `agent/T007-memory`, started exactly at
  base commit `830ab2f`.
- This handoff is committed separately on the same branch.
- Cherry-pick `adff0a3` and the handoff commit onto `master` through the PM
  integration lane. Only `core/zana_core/memory/**`, `core/tests/memory/**`,
  and this handoff are included; no lockfile or shared manifest changed.
- Deferred verification: the runtimes transport suite requires unrestricted
  socket binding, and live chat/memory integration remains for the
  integration lane.
