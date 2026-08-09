# T900 Knowledge Providers Handoff - Docling and LanceDB Local Providers

Verdict: PASS (lead review BLOCK addressed in `d7e9a56`)

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

## Lead review fix (`d7e9a56`)

The lead BLOCK requested seven direct integrity fixes, all applied in one
review-fix commit within the same owned paths:

1. `DoclingParser` now rejects `approved=false` for every kind before any file
   work, and `_preflight_source` verifies exact regular-file size, configured
   `max_source_bytes`, and recorded `SourceMetadata.size_bytes` before the
   SHA-256 loop; hashing is bounded byte-for-byte.  Converter never runs for
   unapproved, size-drifted, or oversized PDFs (tests assert zero converter
   calls).
2. Docling `document.texts` is consumed through a cap+1 iterator; a hostile
   infinite iterable consumes exactly `max_section_count + 1` items and fails
   bounded (test asserts the consumed count).
3. `LanceDBRecordStore.upsert` distinguishes table absence (create, never
   `mode="overwrite"`) from update failure (merge-insert by `chunk_id`; a
   failed merge surfaces `IndexCorruptionError` and never recreates the table).
   Injected backend adapter tests cover create, merge/update, and
   failure-no-overwrite.
4. Real store `load_records` performs a bounded `count_rows` preflight against
   `KnowledgeLimits.max_index_records` before materializing rows; an injected
   table reporting cap+1 rows is rejected without `to_pylist` materialization.
5. Query vectors are validated (exact type, dimensions, finite cells, required
   L2 normalization) before any backend search, LanceDB search selects cosine
   explicitly, and `_distance` is mapped to `1 - distance` with range checks.
   Tests cover wrong dimensions, NaN, non-normalized queries, explicit cosine
   selection, and valid score mapping.
6. `write_snapshot` is immutable: same identity is idempotent after
   verification, different identity fails and preserves existing bytes, and
   final publication failures clean the temporary file.  Tests cover all three.
7. Unavailable parser mapping returns a bounded canonical message and action
   set; arbitrary provider text/actions (including secrets or raw tracebacks)
   are never echoed.  A hostile-exception test covers this.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Smallest provider tests | `pytest core/tests/knowledge/test_docling.py core/tests/knowledge/test_lancedb_index.py -q` | 53 passed |
| Full knowledge tests | `pytest core/tests/knowledge -q` | 257 passed |
| Resource/portability tests | `pytest core/tests/resources core/tests/portability -q` | 165 passed |
| Full Core suite | `pytest core/tests -q` | 1641 passed |
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
- File size gates run before hashing; hashing is byte-bounded; parser
  unavailable errors never echo provider text/actions.
- LanceDB table materialization is gated by a bounded row-count preflight, and
  upserts never overwrite a table after an update failure.

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
- Review-fix commit: `d7e9a56`
  (`fix: harden Docling and LanceDB provider integrity`).
- This handoff is the final docs-only commit on the same branch.
- Merge the eight owned source/test files and this handoff through the PM
  integration lane.  No lockfile, manifest, existing shared contract, API, DB,
  or other lane is included.

## Clean proof

After the implementation and review-fix commits, `git status --porcelain` was
empty.  The handoff docs commit lands last; final index and worktree are clean.

## Push state

Not pushed.  No push was requested for this lane; the local branch commits are
ready for the lead's fetch/reconcile and non-force push decision.
