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
