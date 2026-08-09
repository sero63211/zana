# T900 Knowledge Providers Handoff - Docling and LanceDB Local Providers

Verdict: PASS

## Scope

Delivered the real optional local knowledge-provider area over canonical
knowledge contracts: Docling-backed PDF/Markdown/TXT parsing and a
LanceDB-backed persistent local index with immutable identity, atomic
publication, bounded batches/results/text, provenance preservation, and honest
unavailable/corrupt/incompatible states.  Optional dependencies remain lazy so
ZANA starts without them.  No dependency was added, no manifest/lockfile/API/UI
was changed, and no live model, server, network, or training path was started.

## Changed files and touched modules

Implementation commit `3ecadeb` changed:

- `core/zana_core/knowledge/docling.py` (new) - real optional Docling-backed
  parser with lazy Docling import, real Markdown/TXT parsing through the
  canonical normalizers, strict path/symlink/type/size/digest bounds, and
  honest `PARSER_UNAVAILABLE` for PDF when Docling is absent.
- `core/zana_core/knowledge/lancedb_index.py` (new) - canonical persistent
  local vector index implementing the existing `VectorIndex` contract,
  immutable `IndexIdentity` manifest, atomic write-then-replace publication,
  bounded validation/search, fail-closed create/overwrite/open/upsert
  semantics, provenance preservation, real lazy LanceDB record store, and
  structured unavailable/corrupt/incompatible errors.
- `core/zana_core/knowledge/parsers.py` - `parse_sources` now maps provider
  exceptions carrying an honest `PARSER_UNAVAILABLE` marker to a structured
  unavailable `ParserError` instead of a generic `PARSE_FAILED`.
- `core/zana_core/knowledge/snapshots.py` - added `chunk_config_digest`,
  `build_index_identity`, `write_snapshot`, and `read_snapshot` for durable,
  atomically published snapshot manifests and the index identity bridge.
- `core/zana_core/knowledge/retrieval.py` - added
  `RetrievalService.open_persistent` so persisted indexes can be opened with
  an injected record store and bound to an embedding provider.
- `core/zana_core/knowledge/__init__.py` - exports the new public providers
  without importing optional dependencies.
- `core/tests/knowledge/test_docling.py` (new) - 15 focused tests.
- `core/tests/knowledge/test_lancedb_index.py` (new) - 20 focused tests using
  an exact tiny durable local `RecordStore` fixture.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Smallest provider tests | `pytest core/tests/knowledge/test_docling.py core/tests/knowledge/test_lancedb_index.py -q` | 35 passed |
| Full knowledge tests | `pytest core/tests/knowledge -q` | 239 passed |
| Resource/portability tests | `pytest core/tests/resources core/tests/portability -q` | 165 passed |
| Full Core suite | `pytest core/tests -q` | 1622 passed |
| Ruff lint | `ruff check core/zana_core/knowledge core/tests/knowledge/test_docling.py core/tests/knowledge/test_lancedb_index.py` | clean |
| Ruff format | `ruff format --check core/zana_core/knowledge core/tests/knowledge/test_docling.py core/tests/knowledge/test_lancedb_index.py` | clean |
| Pyright knowledge package | `pyright zana_core/knowledge` | 0 errors, 0 warnings |
| Import smoke | `import zana_core.knowledge`, `import zana_core.main`, public provider imports | pass |
| Diff hygiene | `git diff --check` | pass |

Test coverage includes real Markdown/TXT fixture parsing, unapproved sources,
content-changed-since-intake rejection, oversize/symlink/non-regular/
traversal paths, honest PDF unavailable through `parse_sources`, injected
Docling converter/reader adapter calls, malformed and empty Docling output,
create/open/search round trips, immutable and deterministic identity
manifests, expected-identity incompatibility, corrupt and tampered manifests,
upsert add/dedupe and dimension drift, normalization rejection, search and
record count limits, atomic failure leaving no published manifest, fail-closed
overwrite of an existing index, honest LanceDB unavailability, symlink index
locations, snapshot persistence/read-back, and persistent retrieval
provenance.

## Security delta

- Source reads enforce exact path/symlink/regular-file/byte limits, stream in
  bounded chunks, verify the recorded SHA-256 before parsing, and never modify
  the original file.
- Index manifests are immutable, identity-keyed, written atomically, and
  verified on open; `create` refuses to overwrite an already published index;
  corrupt, tampered, missing, incompatible, or hostile locations fail closed.
- LanceDB/Docling are only imported lazily; absent packages report honest
  unavailable errors, so no fake successful provider path exists.
- Results are bounded to the configured candidate/top-K limits and candidate
  records/scores are revalidated before they reach retrieval.

## Residual risk

- The real LanceDB and Docling package paths are code-complete but were not
  executed because those dependencies are not installed and the scope forbids
  installing or downloading.  Their adapter mapping was reviewed directly and
  the unavailable branches are covered by tests; live-provider verification
  remains a later bounded milestone.
- An adversarial filesystem race between symlink inspection and open is
  minimized by strict component checks but is not separately defended against
  concurrent mutation, matching the existing intake residual risk.

## Blockers

None.

## Merge instructions

- Branch: `agent/t900-knowledge-providers`, started exactly at canonical base
  `9c1dfb4a2ee4b30fe836d795ef4663e3d921bd75`.
- Implementation commit: `3ecadeb`
  (`feat: add Docling and LanceDB local knowledge providers`).
- This handoff is the second, docs-only commit on the same branch.
- Merge the eight owned source/test files and this handoff through the PM
  integration lane.  No lockfile, manifest, existing shared contract, API, DB,
  or other lane is included.

## Clean proof

After the implementation commit, `git status --porcelain` was empty.  The
handoff docs commit lands last; final index and worktree are clean.

## Push state

Not pushed.  No push was requested for this lane; the local branch commits are
ready for the lead's fetch/reconcile and non-force push decision.
