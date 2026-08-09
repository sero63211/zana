# T007 Knowledge Handoff — Safe Intake, Normalization, Chunking, and Snapshots

Verdict: PASS

## Scope

Implemented the bounded low-memory knowledge foundation under
`core/zana_core/knowledge/**` and its focused tests under
`core/tests/knowledge/**`. No DB/API wiring, no embeddings, no LanceDB, no
Docling install/run, and no shared contract change. The authoritative specs
were read from `/Users/sero/Downloads/ZANA_BUILD_PLAN_DETAILED/` per the lead
interface correction.

## Changed files and modules

- `core/zana_core/knowledge/models.py` — typed `DocumentKind`, `ParserWarning`,
  `ParserError`, `SourceMetadata`, `NormalizedSection`, `NormalizedDocument`,
  `Chunk`, `ChunkConfiguration`, `SnapshotManifest`, `EvidenceBlock`, and
  `ContextPackage`.
- `core/zana_core/knowledge/intake.py` — approved-root resolution, traversal
  and final-symlink rejection, readability/type/size checks, streaming
  SHA-256, and an immutable intake copy that never modifies the original.
- `core/zana_core/knowledge/normalizers.py` — deterministic UTF-8 and Markdown
  normalization preserving heading hierarchy, character offsets, and external
  link warnings without fetching links.
- `core/zana_core/knowledge/parsers.py` — explicit `DocumentParser` provider
  protocol with `supported_kinds`; PDF/Docling is honestly reported as
  unavailable rather than faked.
- `core/zana_core/knowledge/chunker.py` — `zana.heading-aware.v1` with
  per-section chunking, section-confined overlap, stable IDs/order, offsets,
  page metadata, and a deterministic `zana.text-estimator.v1`.
- `core/zana_core/knowledge/snapshots.py` — snapshot manifests and invalidation
  digests covering source hash, parser version, chunk config, and required
  embedding identity placeholder without invoking embeddings.
- `core/zana_core/knowledge/evidence.py` — structured evidence rendering with
  stable source locators, delimited untrusted text, and deterministic
  context-budget fitting.
- `core/tests/knowledge/**` — 26 focused tests.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused pytest | `core/.venv/bin/python -m pytest core/tests/knowledge -q` | 26 passed |
| Ruff lint | `core/.venv/bin/ruff check core` | clean |
| Ruff format | `core/.venv/bin/ruff format --check core` | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

Full-suite note: `core/.venv/bin/python -m pytest core/tests -q` reached 211
passed / 1 failed; the failure is the pre-existing hardware lane live Apple
Metal probe (`test_darwin_metal_real_probe`), outside this lane's ownership,
and was not modified.

Test coverage includes hashing, traversal and symlink escape, unsupported,
oversize, and unreadable inputs, Markdown/TXT normalization, chunk boundaries,
section-confined overlap, determinism, evidence rendering, context budgets,
parser unavailability, and snapshot invalidation.

## Security delta

- Approved intake resolves paths inside explicit roots and rejects traversal,
  final symlinks, unreadable files, and oversize files; originals are never
  modified.
- Document content is treated as untrusted data: evidence is delimited and
  cannot grant permissions or alter system policy.
- No embeddings, vector store, network, subprocess, or model process is
  invoked; PDF parsing is not claimed.

## Residual risk

- Symlink containment checks reject the final path component as a symlink and
  rely on `Path.resolve` for intermediate components; an adversarial
  filesystem race between check and read is not separately defended.
- The deterministic estimator is a text-length approximation; a future
  embedding-provider tokenizer can be injected through the recorded estimator
  identity.
- PDF/Docling and embeddings remain intentionally deferred to later lanes.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `4e7274b`
  (`feat: add knowledge intake, normalization, chunking, and snapshots`) on
  branch `agent/T007-knowledge`, started exactly at base commit `830ab2f`.
- This handoff is committed separately on the same branch.
- Merge `core/zana_core/knowledge/**`, `core/tests/knowledge/**`, and this
  handoff through the PM integration lane. No lockfile, manifest, DB, API, or
  other lane is included.
