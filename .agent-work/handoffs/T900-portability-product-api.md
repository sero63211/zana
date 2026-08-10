# Handoff T900-portability-product-api

Verdict: PASS

## Scope

Complete authenticated export/verify/import/delete vertical over the canonical
OCI/archive/import primitives and the persisted Image/Artifact registry. No
`images/**`, shared API/DB, manifest, lock, migration, or main router file was
edited.

## Changed files and modules

- `core/zana_core/portability/__init__.py` — exports `PortabilityProductService`.
- `core/zana_core/portability/import_.py` — optional confined retained-layout
  persistence and exact available-base-digest passthrough.
- `core/zana_core/portability/models.py` — `RegistrationPlan` now carries the
  exact config name/version and base-model registry key used for atomic DB
  registration.
- `core/zana_core/portability/service.py` — new product service: registered
  layout resolution/reconstruction, exact digest-based base availability,
  atomic import registration, export orchestration, reference-checked delete.
- `core/zana_core/api/portability.py` — new authenticated router under
  `/api/v1/images` for verify/export/import/delete.
- `core/zana_core/api/portability_schemas.py` — isolated typed request/response
  models.
- `core/tests/portability/test_product_service.py` — 10 focused product tests.
- `core/tests/api/test_portability_api.py` — 4 focused authenticated router
  tests.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused product/API tests | `pytest -q core/tests/portability core/tests/api/test_portability_api.py` | 120 passed |
| Touched-surface suite | `pytest -q core/tests/portability core/tests/api/test_portability_api.py core/tests/images` | 240 passed |
| Broader API gate | `pytest -q core/tests/api core/tests/portability core/tests/images core/tests/artifacts` | 411 passed; 4 unrelated pre-existing `TestExplicitDatabaseCommit` failures reproduced identically on clean canonical HEAD `23b9034` |
| Ruff check | `ruff check ...` owned paths | clean |
| Ruff format | `ruff format --check ...` owned paths | clean |
| Pyright | `pyright zana_core/portability zana_core/api/portability.py zana_core/api/portability_schemas.py` | 0 errors, 0 warnings |
| Import smoke | `import zana_core.portability`, `zana_core.api.portability` | pass |
| Diff hygiene | `git diff --check` | pass |

No large archive, dependency install, live API/app/browser/runtime/model,
network, broad suite, E2E, performance, or load verification ran.

## Security delta

- Archive/OCI validation, traversal, symlink/hardlink/device, count/size,
  secret, and mutable-state rejection stays entirely in the canonical
  `images` stack; no parallel codec or parser was introduced.
- Export/import outputs and inputs are confined to managed
  `data_root/portability/{exports,imports}` roots; replace requires the exact
  existing-file digest token; symlinks and directory destinations fail closed.
- Imported layouts are retained atomically under
  `data_root/portability/layouts/<digest>` and revalidated against the archive
  plan on duplicate import; conflicting registry records fail closed.
- DB transactions are short: registry snapshots and registration are separate
  from all artifact-store and archive I/O.
- Base-model availability is reported only from the exact persisted
  `models.digest`; display names are never used to infer compatibility.
- Delete removes only `images`/`image_artifacts` rows and refuses images
  referenced by instances; artifact blobs, layouts, model records, and user
  archives are never deleted.
- API responses use data-root-relative paths or basenames, never raw host
  paths, secrets, or tracebacks.

## Residual risk

- The active build task owns image finalization; its future canonical layout
  path (`data_root/images/manifests/<digest>`) is supported if present, and
  otherwise the service reconstructs the exact OCI layout from persisted
  ImageArtifact/Artifact rows. Build-created images lacking both remain
  actionable (`material-missing`/corruption states).
- zstd remains honest: `tar.zst` export/import reports `CODEC_UNAVAILABLE`
  with an `install_zstd` action unless the real package exists; no bytes are
  mislabeled.
- Same-user filesystem TOCTOU between stat and open is mitigated by the
  canonical dirfd/no-follow boundaries but not eliminated for adversarial
  races.
- The 4 `TestExplicitDatabaseCommit` failures reproduce on clean canonical
  HEAD and are outside this ownership.

## Blockers

None.

## Merge instructions

Integrate commit `27a1f61340b97d361a309fdd7b1f71ecd5d2f7c0` onto the PM
integration branch. Minimal serial router delta for lead integration only:

```python
from zana_core.api.portability import router as portability_router

app.include_router(portability_router)
```

Place the import with the other API routers and include the router after
`images_router`. No shared schema/DB/main contract was changed by this commit;
the new router reuses `POST /api/v1/images/{digest}/verify`,
`POST /api/v1/images/{digest}/export`, `POST /api/v1/images/import`, and
`DELETE /api/v1/images/{digest}?confirmed=true`.

## Accepted commit and clean proof

- Implementation commit: `27a1f61340b97d361a309fdd7b1f71ecd5d2f7c0`
- Clean index/worktree proof after implementation commit: `git status
  --porcelain` empty.

## Remote state and push blocker

Not pushed. Explicit push blocker: this task was instructed not to push;
remote acceptance requires lead integration under ZANA remote policy. No
remote SHA is claimed.

## Lead review correction (2026-08-10)

Verdict: PASS after one focused correction commit.

### Correction commit

- `2ce852d7adcf17d02ad257fdfd4fb14ce5243da2`
  (`fix: propagate exact base availability and harden portability API`)

### Changed paths in the correction

- `core/zana_core/portability/boundary.py` — new injected fail-closed
  `OperationBoundary` with typed `OperationCancelledError` (`CANCELLED`).
- `core/zana_core/portability/service.py` — explicit `base_model_available`
  carried from the persisted exact digest set; manifest-authoritative
  reconstruction rejecting duplicate/missing/extra roles, role/media/size/
  digest/path mismatches; atomic sidecar export report; phase-boundary
  progress/cancel checks; exact registered role count.
- `core/zana_core/api/portability.py` and `api/portability_schemas.py` —
  default codec is now `tar.zst`; responses carry
  `base_model_available`, `report_path`, `report_digest`, and exact
  `artifact_count`; request docs state the managed-root staging constraint.
- `core/tests/portability/test_product_service.py` and
  `core/tests/api/test_portability_api.py` — focused regression coverage for
  all six review findings.

### Checks after correction

| Check | Command | Result |
| --- | --- | --- |
| Focused product/API tests | `pytest -q core/tests/portability core/tests/api/test_portability_api.py` | 133 passed |
| Touched-surface suite | `pytest -q core/tests/portability core/tests/api/test_portability_api.py core/tests/images` | 247 passed |
| Ruff check | owned paths | clean |
| Ruff format | owned paths | clean |
| Pyright | owned source paths | 0 errors, 0 warnings |
| Import smoke and diff | `import` smoke, `git diff --check` | pass |

### Revised security and behavior delta

- `base_model_available` is derived from the persisted `models.digest` set
  before import and carried explicitly through `ProductImport`; the API never
  guesses it from `runnable`. A non-base non-runnable result keeps the exact
  availability flag.
- Reconstruction is manifest-authoritative: the registered manifest/index/
  config/layer rows must match descriptors exactly (role, media type, size,
  digest, canonical store path); duplicates, missing/extra roles, and blob
  corruption fail closed as `REGISTRY_MISMATCH`/corrupted states.
- Export defaults to `tar.zst`; when the real zstd codec is absent the API
  returns `CODEC_UNAVAILABLE` with an `install_zstd` action and never labels
  bytes falsely. Explicit `tar` remains a caller choice for fixtures.
- Every export writes a bounded, atomic, secret-free
  `<archive>.report.json` sidecar; `report_digest` and data-root-relative
  `report_path` are returned.
- Verify/export/import accept an injected `OperationBoundary` reporting only
  real phase stages and raising typed `CANCELLED` at phase transitions. The
  canonical pack/unpack loops are not interruptible yet; that requires the
  exact serial integration delta below.
- `artifact_count` is the exact registered role graph size, never
  arithmetic over blob lists.

### Current managed staging constraint

Core only accepts data-root-confined paths: exports under
`data_root/portability/exports` and imports under
`data_root/portability/imports`. A later Tauri/CLI picker-copy integration is
required before arbitrary user-selected external host paths can be passed;
the API schemas and this handoff document that explicitly.

### Exact minimal serial integration delta for cancellation

Full jobs/SSE cancellation is not wired by this slice. The later integration
owner should register an `OperationBoundary` per image job, call it at the
same phase transitions, and map `OperationCancelledError` to the persisted
job status; no shared jobs/main/DB change is needed to consume the boundary.

### Revised residual risks

- The canonical `tar`/`gzip` pack/unpack loops accept only a deadline; real
  mid-loop cancellation requires a future canonical codec boundary.
- zstd availability remains honest and environment-dependent.
- The 4 unrelated `TestExplicitDatabaseCommit` failures reproduce on clean
  canonical HEAD `23b9034` and remain outside this ownership.

### Clean proof after correction

- `git status --porcelain` was empty immediately after correction commit
  `2ce852d` and is re-proven after this handoff receipt commit.

## Lead review correction 2 (2026-08-10)

Verdict: PASS after focused correction commit `1c8101d`.

### Correction commit

- `1c8101d521245187d5708f5c00ab526988f528e3`
  (`fix: harden portability graph validation and export truthfulness`)

### A–G implementation notes

- A: `verify`/`export` now call `_validate_registry_graph` for persistent and
  reconstructed layouts alike. The persisted ImageArtifact/Artifact graph must
  exactly match the validated layout roles, digests, media types, sizes, and
  canonical store paths; duplicate/missing/extra rows fail closed.
- B: `_register_import` now validates every pre-existing global Artifact row
  (media type, size, canonical path) against the exact LayoutRole before any
  Image/ImageArtifact write, and compares the full existing image graph for
  idempotent re-imports. New images with a valid same-digest Artifact reuse it;
  wrong media/size/path, same-role/different-digest, extra/missing rows, and
  duplicate roles fail closed.
- C: `_read_layout_roles` now requires exact `ROLE_MEDIA_TYPES[role]`, unique
  layer roles, and exact manifest/config descriptor media types; the retained
  layout is revalidated against the immutable registration plan immediately
  before registration.
- D: `_available_base_digests` truncation is removed. Base availability is a
  bounded exact lookup for the target digest in both verify and import, and the
  canonical `ImportService` receives the exact callback.
- E: sidecar reports use no-clobber `O_EXCL` plus atomic hard-link publication;
  existing/concurrent sidecars return `REPORT_EXISTS`. Once archive replacement
  is irreversible, sidecar/report/cleanup failures are surfaced truthfully in
  `ProductExport` (`report_written`, `report_warning`, `durability_uncertain`,
  `cleanup_uncertain`) instead of returning a false failure that invites a
  destructive retry. Pre-commit cancellation is the only cancellable point;
  `COMPLETE` is a non-failing post-commit notification.
- F: `_remove_owned_layout` and export output removal now raise typed
  `CLEANUP_UNCERTAIN`; export returns that uncertainty in the result instead of
  masking a successful archive.
- G: import layout roots are confined to `_layouts_root`; `_safe_relative_path`
  is replaced by `_owned_relative_path`, which raises `RESULT_PATH_ESCAPE` /
  `RESULT_PATH_INVALID` for any non-contained service result instead of
  returning a basename.

### Interface delta

`PortabilityExportRead` gains `report_written`, `report_warning`, and
`cleanup_uncertain`; this is an additive isolated API-schema change only and is
reported as INTERFACE for serial main integration.

### Checks

| Check | Command | Result |
| --- | --- | --- |
| Focused product/API tests | `pytest -q core/tests/portability core/tests/api/test_portability_api.py` | 24 passed |
| Touched-surface suite | `pytest -q core/tests/portability core/tests/api/test_portability_api.py core/tests/images` | 251 passed |
| Ruff check/format | owned paths | clean |
| Pyright | owned source paths | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

### Revised residual risks

- Same-user filesystem TOCTOU between DB reads and store opens remains
  mitigated by dirfd/no-follow boundaries but not eliminated for adversarial
  races.
- zstd availability remains honest and environment-dependent.
- The 4 unrelated `TestExplicitDatabaseCommit` failures reproduce on clean
  canonical HEAD and remain outside this ownership.
- Mid-loop cancellation inside canonical pack/unpack still requires the future
  canonical codec boundary; only phase transitions are cancellable.

### Clean proof

- `git status --porcelain` was empty immediately after `1c8101d` and is
  re-proven after this receipt commit.

## Lead review correction 3 (2026-08-10)

Verdict: PASS after focused correction commit `7c459c8`.

### Correction commit

- `7c459c8be500e23480cf77884f6fe2dd0add3286`
  (`fix: structured export cleanup and transaction-local artifact validation`)

### Items addressed

1. Export cleanup is now structured for every exit after temporary layout
   materialization. Any pre-archive validation, graph, cancellation, or
   ExportService failure runs `_cleanup_temporary`; a cleanup failure before
   irreversible archive success raises typed `CLEANUP_UNCERTAIN` chained from
   the original error. After archive success, cleanup failure is surfaced as
   `cleanup_uncertain` in `ProductExport` without masking success.
2. `_write_sidecar_report` now unlinks its owned sibling temp in a `finally`
   path on every failure and success, including the concurrent `FileExistsError`
   from `os.link`, without touching the concurrent report. The focused test
   asserts no `.report.*.tmp` remains after the simulated race.
3. `_register_import` now performs authoritative transaction-local Artifact
   validation (`_validate_artifacts_in_uow`) in the same registration UoW
   immediately before any Image/ImageArtifact write/commit. The earlier
   `_existing_global_artifacts` read remains advisory only; a focused test
   bypasses it and proves the second transaction-local read still blocks a
   conflicting global Artifact.
4. Focused adversarial tests added for validation failure cleanup,
   ExportService failure cleanup, sidecar concurrent FileExists temp hygiene,
   cleanup failure before archive (typed) versus after archive
   (`cleanup_uncertain`), and same-transaction Artifact mismatch.

### Checks

| Check | Command | Result |
| --- | --- | --- |
| Focused product/API tests | `pytest -q core/tests/portability core/tests/api/test_portability_api.py` | 29 passed |
| Touched-surface suite | `pytest -q core/tests/portability core/tests/api/test_portability_api.py core/tests/images` | 256 passed |
| Ruff check/format | owned paths | clean |
| Pyright | owned source paths | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

### Residual risks

- Same-user filesystem TOCTOU between DB reads and store opens remains
  mitigated by dirfd/no-follow boundaries but not eliminated for adversarial
  races.
- Mid-loop cancellation inside canonical pack/unpack still requires the future
  canonical codec boundary.
- The 4 unrelated `TestExplicitDatabaseCommit` failures reproduce on clean
  canonical HEAD and remain outside this ownership.

### Clean proof

- `git status --porcelain` was empty immediately after `7c459c8` and is
  re-proven after this receipt commit.

## Lead canonical integration acceptance (2026-08-10)

Verdict: PASS

- Source commits through `7afee5d` were cherry-picked serially onto clean,
  fetched canonical `main`; canonical product/receipt tip is `9ea7e13`.
- Lead reran the touched surface on canonical main: 256 portability/API/image
  tests passed; Ruff check and format passed; Pyright reported 0 errors and 0
  warnings; `git diff --check` passed.
- Lead directly reviewed exact persistent/reconstructed registry graphs,
  transaction-local Artifact conflict validation, no-clobber sidecar creation,
  cleanup before and after irreversible archive success, target-digest model
  availability, path confinement, and non-failing completion notification.
- Security delta: import/export remain managed-root confined and authenticated;
  corrupt, missing, extra, duplicate, mismatched, secret-bearing, traversal and
  symlink material fails closed; archive success is never reported as a false
  failure merely because report or temporary cleanup is uncertain.
- Residual risk: the isolated router is implemented but intentionally not yet
  registered in `main.py`; that shared-file change belongs to the later serial
  API integration scope. Mid-codec-loop cancellation and live round-trip tests
  remain deferred by host-safety policy.
- Canonical index/worktree were clean after integration. A non-force push was
  confirmed with `git ls-remote`: `origin/main` exactly matched
  `623b265b122827f9a2639f9da915804cbfa0c918` before this final receipt commit.
