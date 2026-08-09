# T007 Images Handoff — Image Config, OCI Layout, and Safe Portability

Verdict: PASS

## Scope

Implemented the T007 images lane under `core/zana_core/images/**`,
`core/tests/images/**`, and `schemas/image-config.schema.json`, building on the
integrated `core/zana_core/artifacts` package. No DB/API wiring, dependency
addition, runtime/model startup, or fake zstd support was introduced.

## Changed files and modules

- `core/zana_core/images/models.py` — versioned `ZanaImageConfig` Pydantic
  models with exact base-model identity requirements, explicit
  `RunnableState` (`runnable`, `not-runnable-missing-base`,
  `not-runnable-weak-identity`, `not-runnable-unknown`), and recursive digest
  validation.
- `core/zana_core/images/oci.py` — deterministic OCI Image Layout assembly
  (`oci-layout`, `index.json`, `manifest.json`, `blobs/sha256`), canonical
  JSON serialization, descriptor sizes, ZANA media types, and digest
  verification for every blob during validation.
- `core/zana_core/images/archive.py` — `ImageCodec` interface, real `tar`
  codec, safe tar extraction rejecting absolute paths, `..`, symlinks,
  hardlinks, device nodes, duplicates, unexpected members, and count/size
  limits; `tar.zst` codec is real only when the `zstandard` package exists,
  never faked.
- `core/zana_core/images/secrets.py` — recursive `ExclusionScanner` for
  secret files and mutable instance state.
- `core/zana_core/images/import_plan.py` — `plan_import` /
  `register_into_store` atomic registration plan/result contract with no DB
  side effects; missing/weak exact base identity remains not runnable.
- `schemas/image-config.schema.json` — JSON Schema for the versioned image
  config.
- `core/tests/images/**` — 46 focused tests.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Images tests | `pytest core/tests/images -q` | 46 passed |
| Owned-surface suite | `pytest core/tests/images core/tests/artifacts core/tests/runtimes core/tests/db core/tests/jobs core/tests/api core/tests/permissions -q` | 178 passed |
| Ruff lint | `ruff check core` | clean |
| Ruff format | `ruff format --check core` | clean |
| Pyright | `pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

The full repo suite reports one unrelated hardware probe failure
(`test_darwin_metal_real_probe` in another lane); it is outside this
ownership and was not modified.

## Residual limitation: tar.zst

`zstandard` is not installed in the shared environment, so no tar.zst archive
was produced or labeled as such. `available_codecs()` honestly reports only
`tar`, and `ZstdTarCodec` raises `CodecUnavailableError` until a real zstd
capability exists. OCI layout assembly, archive safety primitives, import
validation, and atomic registration are fully implemented and tested with the
real tar codec.

## Security delta

- Import extraction is confined to a temporary destination with member
  allowlists, duplicate/unsafe-member rejection, symlink/hardlink/device
  rejection, and count/size limits.
- Every OCI blob, config, manifest, and index is digest-verified; mutation or
  truncation is rejected before registration.
- Secret files and mutable instance state are excluded by name/suffix and by
  recursive directory classification; symlinks in an export root are
  rejected.
- Missing or weak exact base identity imports as explicit not-runnable state;
  a different digest is never substituted.
- No DB rows, secrets, or fake success are produced.

## Residual risk

- The tar round-trip test asserts the two canonical files are present;
  directory members are allowed but not counted as extracted files.
- Archive import requires `plan_import` after `codec.unpack`; the lane exposes
  both primitives and does not wire them into API/DB yet.
- An adversarial filesystem race after extraction is outside the in-process
  archive scope.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `b057125`
  (`feat: add ZANA image config, OCI layout, and safe portability`) on branch
  `agent/T007-images`, started exactly from integrated commit `830ab2f`.
- This handoff is committed separately on the same branch.
- Merge `core/zana_core/images/**`, `core/tests/images/**`,
  `schemas/image-config.schema.json`, and this handoff through the PM
  integration lane. Do not touch `core/uv.lock` or other lane files.
