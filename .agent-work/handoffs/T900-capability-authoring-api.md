# T900 Capability Authoring API Handoff

Verdict: PASS

## Scope

Real end-to-end Capability Source authoring at the Core API boundary: draft
creation with a canonical on-disk workspace, bounded behavior/document/eval
ingestion with atomic replacement, coherent `zana.yaml` persistence, typed
list/detail/source responses without host-path disclosure, and real
`CapabilitySourceValidator` validation of the saved workspace.

Base: `48a016c009c064f4d84460ddd8d8d88f31e0b485` in the isolated worktree
`/Users/sero/.codex/worktrees/f2c9/zana` on branch
`agent/t900-capability-authoring-api`.

## Changed files and touched modules

- `core/zana_core/capabilities/authoring.py` (new): canonical workspace path
  derivation, private workspace creation/rollback, strict local-source path
  validation, streaming SHA-256 copy, deterministic safe manifest serialization
  with round-trip proof, atomic staged publication, and source/manifest backup
  restore on failed publication.
- `core/zana_core/api/capabilities.py`: preserved authenticated list/create/get/
  update routes; draft creation now persists a real `working_dir`; added typed
  `GET /{id}/detail`, `GET /{id}/sources`, `POST /{id}/sources`, and
  `POST /{id}/validate`; source writes are atomic and manifest-coherent.
- `core/zana_core/api/schemas.py`: strict/extra-forbidden `CapabilityCreate` and
  `CapabilityUpdate`; discriminated bounded source-create models; typed source,
  detail, issue, provenance, and validation response models.
- `core/zana_core/db/repositories.py`: additive exact-path source row delete for
  logical-target replacement; no unrelated repository behavior changed.
- `core/tests/api/test_capability_authoring.py` (new): full authoring flow,
  safety, rollback, atomic replacement, disclosure, auth, and manifest
  coherence tests.
- `core/tests/capabilities/test_authoring.py` (new): workspace/path/staging/
  publishing/manifest unit tests.

Touched tables: reads/writes existing `capabilities` and `capability_sources`
rows only. No migration.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| New focused tests | `core/.venv/bin/python -m pytest tests/capabilities/test_authoring.py tests/api/test_capability_authoring.py -q` | 46 passed |
| Owned-surface selector | `core/.venv/bin/python -m pytest tests/capabilities tests/api tests/db/test_repositories.py -q` | 207 passed |
| Ruff lint | `core/.venv/bin/ruff check <all owned implementation and test files>` | clean |
| Ruff format | `core/.venv/bin/ruff format --check <all owned files>` | clean |
| Pyright | `core/.venv/bin/pyright zana_core/capabilities/authoring.py zana_core/api/capabilities.py zana_core/api/schemas.py zana_core/db/repositories.py` | 0 errors, 0 warnings |
| Import smoke | `core/.venv/bin/python -c "import zana_core.main, zana_core.api.capabilities, zana_core.capabilities.authoring"` | pass |
| Diff hygiene | `git diff --check` | pass |

No broad/full Core suite, live API server, provider, browser, app, bundle,
runtime, model, download, inference, training, load, GPU/RAM, container, or
performance verification ran under the host-safety policy.

## Security delta

- Draft workspaces are Core-derived `capabilities/<id>` under the app data
  root, validated absolute/contained, private (`0700` dirs, `0600` files), and
  rejected if symlinked or escaping.
- Local document copy requires explicit `user_approved: true`; source paths must
  be absolute forward-slash regular files, reject NUL/control, traversal, dot/
  parent components, workspace-internal paths, symlinks, and oversize files.
- Content is staged, hashed (SHA-256), byte-capped, never executed, and
  published with `os.replace`; a failed file or DB step preserves the prior good
  source/manifest and cleans temp files.
- Destination paths are fixed by kind plus sanitized filename; arbitrary
  destinations and absolute paths are never accepted.
- Detail/source/validation responses expose only relative paths, basenames,
  digests, metadata, and sanitized issue messages; full workspace/data-root host
  paths and document contents are never returned.
- Mutating models are strict/typed/bounded with extra fields forbidden; existing
  list/create/get/update route responses stay byte-compatible.
- On-disk `zana.yaml` and persisted `manifest_json` are kept coherent; divergence
  is reported as an actionable `MANIFEST_DIVERGED` failure instead of silently
  overwriting.

## Residual risk

- Publication/restore relies on same-filesystem atomic `os.replace`; an
  adversarial filesystem race between validation and read is not separately
  defended.
- Evaluation validation is the canonical built-in scorer contract; extending
  scorer types requires the canonical capabilities/evaluation lane update.
- No live desktop save/reopen against the running Tauri app, PDF parsing, or
  build/evaluation execution was performed under host-safety limits.
- `CapabilityRead` still exposes the existing `working_dir` host path by prior
  contract; the new detail/source responses do not.

## Blockers

None.

## Merge instructions

Merge only the six changed files plus this handoff through the PM integration
lane. Do not merge any migration, `core/pyproject.toml`, `core/uv.lock`, `main.py`
router registration, or other lane files from this branch. After merge, run the
owned-surface selector above.

## Accepted commit

Implementation commit: `1dfc905` (`feat: add canonical capability authoring API vertical`).
Handoff commit: separate receipt commit below.

## Remote and push state

No push performed by this worker; worker lanes do not push under the current
isolation policy. Observed at receipt: `origin/main` resolved to
`9f46712af3cdd19cfd8dec8d8292269cced33a88` (advanced externally by another
parallel lane after this worker started from the required base
`48a016c009c064f4d84460ddd8d8d88f31e0b485`). Local HEAD on
`agent/t900-capability-authoring-api` is the receipt commit after this handoff.

## Clean proof

Captured after the receipt commit below: index exit 0, worktree exit 0,
`git status --porcelain` empty.

## Correction 3: Final focused safety/resource correction

Verdict: PASS

The lead review found one final set of concrete safety/resource defects in the
accepted Capability Authoring vertical. This correction resolves them and is
committed separately on the same branch without amending prior commits.

### Changes

- `core/zana_core/capabilities/authoring.py`
  - Added fail-closed `ValidationPreflight`: deterministic lstat-only managed
    tree validation with bounded incremental `iterdir` iteration (stops at
    `max_files + 1`), file-count and aggregate-byte limits
    (`MAX_VALIDATION_FILE_COUNT=512`, `MAX_VALIDATION_TREE_BYTES=128 MiB`), and
    symlink/non-regular rejection. No file content is loaded during preflight.
  - Replaced `shutil.copyfile` backup with descriptor-based `O_NOFOLLOW`
    streaming backup: initial/open/final identity+size+mtime checks, streaming
    byte cap, `fsync`, private mode, and explicit per-kind backup caps via
    `backup_max_for_target`; never follows a swapped symlink and rejects
    oversize before unbounded copy.
  - `sanitize_message` no longer calls `replace("", ...)` and never throws on
    resolution failure; it keeps a fixed generic absolute-path redaction and
    bounded fallback.
  - Removed unused duplicate `MAX_SOURCE_*` constants; one coherent
    `MAX_VALIDATION_*` set remains.

- `core/zana_core/api/capabilities.py`
  - `validate_capability` is now gated: a failed tree/preflight result returns
    truthful invalid issues and never calls `load_manifest_dict` or the
    CapabilitySourceValidator; a bounded manifest safety failure also prevents
    validator execution.
  - `add_capability_source` initializes and discards both backup variables on
    every staging/error path, preventing partial backup leaks.
  - Create cleanup now returns `ROLLBACK_UNCONFIRMED` when a newly-created
    workspace cannot be removed in AuthoringError and OSError branches.

- Focused regressions added in `core/tests/capabilities/test_authoring.py` and
  `core/tests/api/test_capability_authoring.py` for external canaries with zero
  validator calls, oversize/count preflight gating, monkeypatched bounded
  backups, sanitizer resolution failure, partial backup cleanup, create
  rollback honesty, and bounded iterator stopping.

### Verification

| Check | Command | Result |
| --- | --- | --- |
| New focused tests | `core/.venv/bin/python -m pytest tests/capabilities/test_authoring.py tests/api/test_capability_authoring.py -q` | 74 passed |
| Owned-surface selector | `core/.venv/bin/python -m pytest tests/capabilities tests/api tests/db/test_repositories.py -q` | 235 passed |
| Ruff lint | `core/.venv/bin/ruff check <all owned implementation and test files>` | clean |
| Ruff format | `core/.venv/bin/ruff format --check <all owned files>` | clean |
| Pyright | `core/.venv/bin/pyright zana_core/capabilities/authoring.py zana_core/api/capabilities.py zana_core/api/schemas.py zana_core/db/repositories.py` | 0 errors, 0 warnings |
| Import smoke | `core/.venv/bin/python -c "import zana_core.main, zana_core.api.capabilities, zana_core.capabilities.authoring"` | pass |
| Diff hygiene | `git diff --check` | pass |

No broad/full Core suite, live API server, provider, browser, app, bundle,
runtime, model, download, inference, training, build, load, GPU/RAM,
container, or performance verification ran under the host-safety policy.

### Residual risk

- A same-user race between the lstat preflight and the validator remains
  possible in principle; the normal validation path is bounded and
  symlink-safe, and no unbounded read/hash can occur before validator calls.
- No live desktop save/reopen, PDF parsing, or build/evaluation execution was
  performed under host-safety limits.

### Commits

- Correction implementation commit: `d382155`
  (`fix: bound capability validation preflight and backup paths`)
- Final receipt commit: `01d47b4`
  (`docs: update T900 capability authoring handoff with final correction`)

Local HEAD on `agent/t900-capability-authoring-api` is `01d47b4`. No push
performed; worker lanes do not push under the current isolation policy.

## Correction 4: Global recursive preflight

Verdict: PASS

Lead review found that the prior preflight only scanned three managed
directories with per-directory counters, so root/auxiliary/nested files could
still reach the validator. This small correction makes the preflight a bounded
incremental recursive walk of the complete workspace.

### Changes

- `core/zana_core/capabilities/authoring.py`
  - `validate_source_preflight` now walks the complete workspace tree with a
    single global file counter and aggregate byte counter, including
    `zana.yaml`, root auxiliary files, and arbitrary nested directories.
  - Iteration is bounded and incremental; the walk stops globally once
    `max_files + 1` proves overflow and never materializes an unbounded
    directory.
  - Every symlink and non-regular entry is rejected; directories are traversed
    no-follow; a depth bound (`MAX_VALIDATION_DEPTH=32`) prevents nesting from
    evading the cap.
  - `zana.yaml` uses its manifest byte cap; all other files use the global
    aggregate cap, and the aggregate itself is always enforced.
- Focused regressions: combined directories exceed the global file limit; huge
  root auxiliary file blocks with zero validator call; nested files are
  counted; the iterator stops globally instead of consuming the directory.

### Verification

| Check | Command | Result |
| --- | --- | --- |
| New focused tests | `core/.venv/bin/python -m pytest tests/capabilities/test_authoring.py tests/api/test_capability_authoring.py -q` | 79 passed |
| Owned-surface selector | `core/.venv/bin/python -m pytest tests/capabilities tests/api tests/db/test_repositories.py -q` | 240 passed |
| Ruff lint/format | `core/.venv/bin/ruff check` and `ruff format --check` on owned files | clean |
| Pyright | changed implementation modules | 0 errors, 0 warnings |
| Import smoke | `import zana_core.main, zana_core.api.capabilities, zana_core.capabilities.authoring` | pass |
| Diff hygiene | `git diff --check` | pass |

No broad/full suite or live/heavy verification ran under host-safety policy.

### Commits

- Correction implementation commit: `98edd12`
  (`fix: make capability validation preflight global and recursive`)
- Final receipt update: `a2b1aa4`
  (`docs: update T900 capability authoring handoff with recursive preflight`)

## Correction 5: Manifest backup phase integrity

Verdict: PASS

Lead review found one proven data-integrity blocker: `update_capability()`
could run compensation when manifest backup failed before publication, and for
an existing workspace the old helper interpreted `None` as no prior manifest and
deleted the untouched `zana.yaml`.

### Changes

- `core/zana_core/api/capabilities.py`
  - Manifest backup staging is now an explicit pre-publication phase; a
    backup-stage failure on an existing manifest returns the original typed
    authoring error and never runs compensation, so the untouched `zana.yaml`
    stays byte-for-byte intact.
  - A newly-created workspace with a backup-stage failure removes only the
    request-created workspace before reporting the original error; if removal
    cannot be proven it returns `ROLLBACK_UNCONFIRMED`.
  - `_compensate_update_manifest` now requires a staged backup for an existing
    workspace and raises `ROLLBACK_UNCONFIRMED` instead of deleting a manifest
    when no backup exists.
  - Post-publication/DB-commit compensation behavior is unchanged: existing
    manifests with backups are restored, new targets are removed, new
    workspaces are removed, and unconfirmed cleanup is honest.
- Focused regressions force `stage_backup` to fail before publication and prove
  the original manifest remains byte-identical and the DB update is not
  accepted. Existing post-publication/commit compensation tests remain green.

### Verification

| Check | Command | Result |
| --- | --- | --- |
| New focused tests | `core/.venv/bin/python -m pytest tests/capabilities/test_authoring.py tests/api/test_capability_authoring.py -q` | 81 passed |
| Owned-surface selector | `core/.venv/bin/python -m pytest tests/capabilities tests/api tests/db/test_repositories.py -q` | 242 passed |
| Ruff lint/format | `core/.venv/bin/ruff check` and `ruff format --check` on owned files | clean |
| Pyright | changed implementation modules | 0 errors, 0 warnings |
| Import smoke | `import zana_core.main, zana_core.api.capabilities, zana_core.capabilities.authoring` | pass |
| Diff hygiene | `git diff --check` | pass |

No broad/full suite or live/heavy verification ran under host-safety policy.

### Commits

- Correction implementation commit: `a32122e`
  (`fix: preserve untouched manifest on backup-stage failure`)
- Final receipt update: `ae4f605`
  (`docs: update T900 capability authoring handoff with manifest phase integrity`)

Local HEAD on `agent/t900-capability-authoring-api` is `ae4f605`. No push
performed; worker lanes do not push under the current isolation policy.

## Correction 6: Manifest compensation state matrix

Verdict: PASS

Lead review found a compensation regression in `a32122e`: after a successful
pre-publication backup phase, `manifest_backup=None` means there was no prior
manifest. That state is possible even when the workspace already existed. The
previous helper conflated workspace existence with manifest existence and
raised `ROLLBACK_UNCONFIRMED` instead of removing the newly created manifest.

### Changes

- `core/zana_core/api/capabilities.py`
  - `_compensate_update_manifest` now treats a completed backup phase
    explicitly: `manifest_backup` present restores the prior manifest; absent
    removes only the newly created `zana.yaml`; a newly created workspace is
    removed only when this request created it.
  - The pre-publication backup failure path is unchanged: it never runs
    compensation and preserves an untouched existing manifest byte-for-byte.
- Focused regression: existing canonical workspace without `zana.yaml`,
  publication succeeds, injected DB commit failure; the new manifest is
  removed, the pre-existing workspace remains, DB state is unchanged, and the
  response is the truthful `DATABASE_COMMIT_FAILED`. The two backup-stage
  failure tests remain green.

### Verification

| Check | Command | Result |
| --- | --- | --- |
| New focused tests | `core/.venv/bin/python -m pytest tests/capabilities/test_authoring.py tests/api/test_capability_authoring.py -q` | 82 passed |
| Owned-surface selector | `core/.venv/bin/python -m pytest tests/capabilities tests/api tests/db/test_repositories.py -q` | 243 passed |
| Ruff lint/format | `core/.venv/bin/ruff check` and `ruff format --check` on owned files | clean |
| Pyright | changed implementation modules | 0 errors, 0 warnings |
| Import smoke | `import zana_core.main, zana_core.api.capabilities, zana_core.capabilities.authoring` | pass |
| Diff hygiene | `git diff --check` | pass |

No broad/full suite or live/heavy verification ran under host-safety policy.

### Commits

- Correction implementation commit: `e9595c7`
  (`fix: compensate absent prior manifest by removing new target`)
- Final receipt update: `e609bed`
  (`docs: update T900 capability authoring handoff with compensation matrix`)

Local HEAD on `agent/t900-capability-authoring-api` is `e609bed`. No push
performed; worker lanes do not push under the current isolation policy.

## Correction 2: Lead review hardening

Verdict: PASS

The lead review found direct integrity/security defects in the accepted
Capability Authoring vertical. This correction resolves all listed items and
is committed separately on the same branch without amending or replacing the
three prior commits.

### Changes

- `core/zana_core/capabilities/authoring.py`
  - Exact canonical workspace enforcement: every operation validates the full
    managed chain (`data_root`, `capabilities`, capability id, and managed
    source directories) with `lstat`, rejecting symlink and non-directory
    components before creation, staging, temp creation, publish, backup,
    restore, manifest load/remove, and validator use.
  - `remove_workspace` now removes only a workspace proven newly created by
    that request and never follows or removes a symlink target; pre-existing
    or unowned workspaces are never reused or deleted.
  - `stage_backup` distinguishes a missing target from a symlink target and
    returns a typed `TARGET_SYMLINK` failure; `remove_file_target` uses no-follow
    `lstat` and never removes a symlink.
  - `restore_backup` no longer swallows failure: it validates the staged backup,
    restores atomically, verifies the restored regular file, and raises
    `SOURCE_RESTORE` on any unconfirmed restoration.
  - Publication/restore `chmod` uses no-follow descriptor-based `fchmod`; managed
    directory privacy uses no-follow `O_DIRECTORY` `fchmod`.
  - `load_manifest_dict` lstat-bounds and reads at most `MAX_MANIFEST_BYTES + 1`,
    rejects symlinks/types/oversize with typed errors, and never loads an
    arbitrary file.
  - Authoring error messages are fixed/actionable and no longer embed raw
    OSError host paths; `safe_issue_file` suppresses arbitrary host paths in
    issue file labels.
  - Document copy is strengthened with no-follow final open on POSIX plus
    descriptor-based dev/ino/size/mtime checks before and after copy, so
    same-size replacement/mutation and final symlink swap fail closed as
    `SOURCE_DRIFT`/typed rejection where the platform allows.

- `core/zana_core/api/capabilities.py`
  - Filesystem-mutating create/update/source routes now call an explicit
    `uow.commit()` inside the route; on commit failure they restore or remove
    exactly the files this request changed and never leave file success paired
    with a rolled-back DB transaction.
  - Create rejects a pre-existing canonical workspace and removes only a
    workspace newly created by the request; update restores the prior
    `zana.yaml` (including empty/remove cases); source restores prior source and
    manifest, or removes newly-created targets when no prior target existed.
  - First-ingest second-publish failure removes the newly-created target;
    restoration/cleanup uncertainty returns a stable `ROLLBACK_UNCONFIRMED`
    error instead of claiming preservation.
  - `load_manifest_dict` failures, divergence, and stage/backup failures now
    also discard a workspace newly created by a source request.
  - Validation reports honest `issue_count` (total) and `returned_issue_count`
    (bounded list), and sanitizes all issue messages/files.

- `core/zana_core/api/schemas.py`
  - Added `returned_issue_count` to `CapabilityValidationRead` for truthful
    truncation reporting.

- `core/tests/capabilities/test_authoring.py` and
  `core/tests/api/test_capability_authoring.py`
  - Added focused regressions for symlinked capabilities/source parent escape,
    rollback never deleting symlink targets or pre-existing workspaces,
    first-source second-publish cleanup, explicit DB commit failure for
    create/update/source, restoration failure honesty, symlink target
    rejection, oversized manifest bounded read, approved-path error redaction,
    issue-count truncation truth, and same-size/source-identity drift.

### Correction verification

| Check | Command | Result |
| --- | --- | --- |
| New focused tests | `core/.venv/bin/python -m pytest tests/capabilities/test_authoring.py tests/api/test_capability_authoring.py -q` | 62 passed |
| Owned-surface selector | `core/.venv/bin/python -m pytest tests/capabilities tests/api tests/db/test_repositories.py -q` | 223 passed |
| Ruff lint | `core/.venv/bin/ruff check <all owned implementation and test files>` | clean |
| Ruff format | `core/.venv/bin/ruff format --check <all owned files>` | clean |
| Pyright | `core/.venv/bin/pyright zana_core/capabilities/authoring.py zana_core/api/capabilities.py zana_core/api/schemas.py zana_core/db/repositories.py` | 0 errors, 0 warnings |
| Import smoke | `core/.venv/bin/python -c "import zana_core.main, zana_core.api.capabilities, zana_core.capabilities.authoring"` | pass |
| Diff hygiene | `git diff --check` | pass |

No broad/full Core suite, live API server, provider, browser, app, bundle,
runtime, model, download, inference, training, build, load, GPU/RAM,
container, or performance verification ran under the host-safety policy.

### Correction security delta

- Symlink/non-directory managed components are rejected at every filesystem
  boundary; rollback never follows symlinks and never deletes pre-existing or
  unowned workspaces.
- DB commits are explicit and compensated: no file success escapes a failed DB
  commit; restoration is confirmed or reported honestly as unconfirmed.
- Approved document copying is descriptor-pinned with no-follow open on POSIX,
  detecting same-size replacement, mutation, and final symlink swap.
- Error paths no longer expose arbitrary host paths; manifest reads are
  byte-bounded and fail closed.

### Correction residual risk

- A TOCTOU race between validation and a single filesystem operation is not
  independently defended beyond lstat/no-follow checks at each operation
  boundary; a future platform-level dirfd/fd-only implementation can close that
  residual gap.
- No live desktop save/reopen, PDF parsing, or build/evaluation execution was
  performed under host-safety limits.

### Correction commits

- Correction implementation commit: `b503b62` (`fix: harden capability authoring containment and commit atomicity`)
- Updated receipt commits: `287b49a` (correction evidence) and `f528726`
  (receipt finalization); local HEAD is the commit containing this finalized
  handoff.

Local HEAD on `agent/t900-capability-authoring-api` is the updated receipt
commit after this section. No push performed; worker lanes do not push under
the current isolation policy.
