# Handoff T007-portability-canonical-integration

Verdict: PASS

## Scope

Manual reconciliation of the final portability/image behavior from read-only
reference `863a84e` onto canonical base `0c63112`, followed by lead integration
onto current `master`. No broad reference-branch merge or rebase was used.

## Changed files and modules

- `core/zana_core/portability/**` and `core/tests/portability/**`
- `core/zana_core/images/{__init__,archive,import_plan,models,oci,secrets}.py`
- `core/tests/images/{test_archive,test_import_plan,test_models,test_oci}.py`
- `core/tests/instances/test_runtime_selection.py`

## Checks and evidence

- Worker focused images/portability: 227 passed.
- Lead focused images/portability/instance compatibility: 235 passed.
- Worker and lead full Core suites: 1588 passed. The lead rerun required only
  local `127.0.0.1` permission for four transport-fixture tests; no external
  network, runtime, model, inference, download, or training was used.
- Ruff check and format: all checks passed, 25 files formatted.
- Pyright images/portability: 0 errors, 0 warnings.
- Import smoke, `git diff --check`, direct security/error/serialization
  inspection: PASS.

## Serialization and compatibility decision

Permission allow-lists remain arrays, including when empty, never `null`.
Nested image models are immutable; two legacy instance tests now construct
variants through nested `model_copy(update=...)` rather than mutating trusted
image state. Production instance callers required no change.

## Security delta

- Bounded streaming archive I/O and strict exact-type limits.
- Canonical OCI/digest verification and exact missing-base-model state.
- Traversal, symlink-component, control-path, and secret/state exclusion.
- Dirfd-confined no-follow atomic export/import/cleanup with owned rollback.
- Narrow permission-reference container exception; other sensitive containers
  and non-string sensitive values remain rejected.

## Residual risk

The portability boundary is a large security-sensitive implementation. Its
unit/integration suite is comprehensive, but the final desktop/API-driven
archive round trip and a real imported-image rerun remain T900 acceptance work.
Zstandard availability is still reported honestly and no dependency was added.

## Accepted commits and clean proof

- Worker implementation: `7cc22330206f13d82fe8291da171ed4d90e472f5`
- Worker receipt: `0e2497f22a8cc14440f1ca1d2b34bcb37a678a62`
- Canonical implementation: `f4d58e90f5e0535616908c4ac3df3e9a26aace2b`
- Canonical index/worktree was clean after the implementation commit.

## Remote state

- Remote: `origin` (`https://github.com/sero63211/zana.git`)
- Non-force push confirmed: `origin/main` =
  `f4d58e90f5e0535616908c4ac3df3e9a26aace2b` immediately after product push.

## Next integration

T900 may now expose the canonical service through typed API/UI flows and prove
the real export/import/rerun acceptance path. Do not duplicate archive, digest,
secret-scanning, or path-confinement logic outside this boundary.
