# T007-capabilities Handoff — Canonical Capability Source Validation

Verdict: PASS

## Scope

Implemented the canonical editable Capability Source validation lane for the
low-memory architecture wave, starting exactly from integrated commit
`9e36e4c`. Only owned paths were touched:

- `core/zana_core/capabilities/**`
- `core/tests/capabilities/**`
- `schemas/capability.schema.json`
- `schemas/evaluation.schema.json`
- `.agent-work/handoffs/T007-capabilities.md`

No database/API wiring, document ingestion, model start, training, or
dependency installation was performed. Only existing dependencies are used
(stdlib, Pydantic, PyYAML from the existing lockfile).

## Changed files and modules

- `core/zana_core/capabilities/manifest.py` — frozen Pydantic models for
  `zana.yaml` (schemaVersion 1, kind `ZanaCapability`, id, name, SemVer,
  compatibility, goal, behavior, knowledge, training, tools, permissions,
  evaluation, verification gates), strict semver/id/safe-path validation,
  and a duplicate-key-rejecting safe YAML loader used for every YAML file.
- `core/zana_core/capabilities/paths.py` — project-root-relative path
  resolution rejecting absolute paths, drive prefixes, backslashes, empty/dot
  segments, `..` traversal, symlink escapes, and missing/type-mismatched
  targets; deterministic package scan rejecting directory symlinks and
  `hooks/` directories; prohibited executable/hook content detection
  (code suffixes, build manifests, executable bits, shebangs).
- `core/zana_core/capabilities/behavior.py` — behavior file loading and
  SHA-256 hashing with UTF-8 checks; content is never executed.
- `core/zana_core/capabilities/training.py` — stable training JSONL
  validation (train/validation roles, record structure, allowed roles,
  provenance metadata, duplicate IDs) with file/line recovery errors.
- `core/zana_core/capabilities/evaluation.py` — stable evaluation JSONL
  validation against the canonical built-in scorer registry from spec 12,
  with parameter type checks and file/line recovery errors.
- `core/zana_core/capabilities/leakage.py` — immutable held-out leakage
  report: shared file detection across declared train/validation/evaluation
  paths and duplicate record ids across splits. `allow_test_overrides` is a
  test-only escape that relaxes only shared-file checks, never record
  identity.
- `core/zana_core/capabilities/provenance.py` — immutable `SourceProvenance`
  with SHA-256, size, role, title (manifest or file-stem origin), declared
  license/usage metadata, and ingestion timestamp; `rights_inferred` is
  always false.
- `core/zana_core/capabilities/validator.py` — `CapabilitySourceValidator`
  orchestrating all stages into one immutable `CapabilitySourceValidation`
  result or an aggregate `CapabilitySourceValidationError` with every issue.
- `schemas/capability.schema.json`, `schemas/evaluation.schema.json` —
  Draft 2020-12 JSON Schemas with `additionalProperties: false` at every
  boundary, matching runtime validation (const 1, kind, semver/safe-path
  patterns, scorer enum and per-type parameter constraints).
- `core/tests/capabilities/**` — 75 focused tests covering the two provided
  example packages (validated verbatim), malformed YAML/JSONL, unsupported
  schema, traversal, symlink escape, hidden code/install hooks, duplicate and
  leaked ids, missing optional paths, deterministic hashes, immutability,
  and schema/runtime parity.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused lane tests | `core/.venv/bin/python -m pytest core/tests/capabilities -q` | 75 passed |
| Full Core suite (regression) | `core/.venv/bin/python -m pytest core/tests -q` | 112 passed |
| Ruff lint | `core/.venv/bin/ruff check core/zana_core/capabilities core/tests/capabilities` | clean |
| Ruff format | `core/.venv/bin/ruff format --check core/zana_core/capabilities core/tests/capabilities` | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core/capabilities` | 0 errors, 0 warnings |
| Schema parse | `json.load` on both schemas | pass |
| Diff hygiene | `git diff --check` | pass |
| Provided examples | `CapabilitySourceValidator().validate(.../examples/math-capability)` and `policy-capability` | both PASS (4 and 6 files hashed, 2 and 3 eval records, leakage ok) |

Verification used the shared existing venv at `/Users/sero/Documents/zana/core/.venv`;
no dependencies were installed.

## Security delta

- Capability packages are validated as data only: no behavior/tool/permission
  content is ever executed; code files, install hooks, shell/Python content,
  executable bits, and shebangs are rejected (`HOOK_PROHIBITED`).
- Path resolution is root-confined: traversal, absolute paths, symlink
  escapes, and directory symlinks fail closed.
- Held-out leakage is prevented: train/validation/evaluation files and record
  ids cannot be shared across splits, with a test-only override that never
  relaxes record identity.
- Duplicate YAML keys and unknown manifest/scorer fields fail closed instead
  of being silently ignored.
- Provenance records declared license/usage metadata only; ZANA never infers
  rights (`rights_inferred: false`).

## Residual risk

- Scorer parameter contracts (`exact_string`, `case_normalized_exact`,
  `numeric_exact`, `numeric_tolerance`, `regex`, `contains_all`,
  `json_schema_valid`, `classification_label`, `citation_required`,
  `source_grounding`) are the V1 canonical registry; extending the scorer set
  requires updating `SCORER_TYPES`, the evaluation schema, and tests together.
- Auxiliary `tools.yaml`/`policy.yaml` content schemas remain owned by the
  T007-permissions lane; this lane only verifies they are single-document YAML
  mappings.
- `core/uv.lock` and `core/pyproject.toml` were not touched; the PM-owned
  lockfile already contains PyYAML via `uvicorn[standard]`.
- Knowledge documents are hashed but not parsed; parsing/ingestion is a later
  lane.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `a86ea81` (`feat: add canonical capability source validation`)
  on branch `agent/T007-capabilities`, started exactly at integrated commit
  `9e36e4c`.
- This handoff is committed separately on the same branch.
- Merge through the PM integration lane. Do not merge `core/uv.lock` or
  `core/pyproject.toml` from this branch. After merge, run the focused lane
  tests plus the full Core suite listed above.
