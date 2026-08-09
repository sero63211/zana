# T007 Artifacts Handoff — Immutable Content-Addressed Store Foundation

Verdict: PASS

## Scope

Implemented the T007 artifact-store foundation under `core/zana_core/artifacts/**`
and its focused tests under `core/tests/artifacts/**`. No DB, API, OCI,
export/import, image registration, runtime, or model work was added, and no
frozen T005 contract was changed.

## Changed files and modules

- `core/zana_core/artifacts/digest.py` — canonical `sha256:<lowercase hex>`
  validation (`validate_digest`), hex normalization (`digest_from_hex`),
  in-memory digest (`digest_bytes`), and bounded-memory streaming digest
  (`digest_stream`), plus explicit `ArtifactError`, `InvalidDigestError`,
  `ArtifactNotFoundError`, `DigestMismatchError`, `RootEscapeError`, and
  `ArtifactCorruptedError`.
- `core/zana_core/artifacts/store.py` — `ArtifactStore` with deterministic
  `blobs/sha256/<hex>` layout, atomic same-filesystem `put_bytes` /
  `put_file` / `put_stream`, flush + `fsync`, digest verification before
  atomic rename, identical-content deduplication, `open` / `read` / `exists` /
  `size` / `verify` / `delete` primitives, symlink/path traversal/root escape
  rejection on every store path boundary, symlinked-source rejection, and a
  `temporary_workspace` / `store.workspace()` context that cleans up without
  deleting user directories.
- `core/tests/artifacts/__init__.py`, `core/tests/artifacts/test_digest.py`,
  `core/tests/artifacts/test_store.py` — 35 focused tests.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused tests | `pytest core/tests/artifacts -q` | 35 passed |
| Full Core suite | `pytest core/tests -q` | 72 passed |
| Ruff lint | `ruff check core` | clean |
| Ruff format | `ruff format --check core` | clean |
| Pyright | `pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

Test coverage includes deterministic SHA-256 digests, deduplication,
concurrent same-content writes, concurrent distinct-content writes, atomic
failure cleanup with no leftover blobs/temps, mutated and truncated blob
rejection, missing-blob errors, traversal digests, symlink blob and
symlinked-directory escapes, symlinked source rejection, root confinement, and
safe temporary workspace cleanup that preserves user directories.

## Security delta

- Blob reads use `O_NOFOLLOW` and every path boundary is resolved and confined
  under the configured artifact root; symlink escapes raise `RootEscapeError`.
- Content is immutable: identical bytes deduplicate to one digest path, and a
  corrupted existing blob is never silently replaced.
- Temporary workspace cleanup refuses to remove the root or anything outside
  it.
- No secrets, DB rows, or fake data are produced; the store is standalone.

## Residual risk

- `read()` verifies the full blob before returning, which is bounded by the
  caller already materializing the bytes in memory; `open()`/`verify()` remain
  streaming.
- Intermediate-directory symlink swaps between confinement checks and open are
  mitigated by final-component `O_NOFOLLOW` and resolve-based confinement but
  are not defended against an adversarial filesystem race beyond that.
- No reference counting or GC exists yet; `delete()` intentionally relies on
  callers confirming a blob is unreferenced.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `5b274f6`
  (`feat: add content-addressed artifact store foundation`) on branch
  `agent/T007-artifacts`, started exactly from integrated commit `9e36e4c`.
- This handoff is committed separately on the same branch.
- Merge `core/zana_core/artifacts/**`, `core/tests/artifacts/**`, and this
  handoff through the PM integration lane. Do not touch `core/uv.lock` or any
  other lane in this batch.
