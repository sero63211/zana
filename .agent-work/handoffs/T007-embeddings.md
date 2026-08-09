# T007 Embeddings Handoff — Provider Boundary and Deterministic Retrieval

Verdict: PASS

## Scope

Implemented the embedding provider boundary and deterministic retrieval
foundation exactly under the owned new files:
`core/zana_core/knowledge/embeddings.py`,
`core/zana_core/knowledge/retrieval.py`,
`core/tests/knowledge/test_embeddings.py`,
`core/tests/knowledge/test_retrieval.py`, and this handoff. No existing
knowledge/runtime/API/DB/manifest/lockfile file was edited, no dependency was
added, and no live Ollama/model/embedding call was made.

## Changed files and modules

- `core/zana_core/knowledge/embeddings.py` — immutable `EmbeddingIdentity`,
  `EmbeddingBatch`, `VectorRecord`, `IndexIdentity`, `EmbeddingLimits`,
  provider/index protocols, structured errors, deterministic L2 normalization
  and cosine, strict vector/index validation, and real `OllamaEmbeddingProvider`
  over the integrated injected bounded HTTP transport with exact `/api/embed`
  request/response parsing.
- `core/zana_core/knowledge/retrieval.py` — `RetrievalQuery/Hit/Result`,
  `RetrievalSmokeRecord`, `index_compatible`, and `RetrievalService` with
  top-K, score threshold, deterministic tie order, explicit document/section
  dedup, stable provenance, and honest smoke-test records.
- `core/tests/knowledge/test_embeddings.py` — 13 focused tests.
- `core/tests/knowledge/test_retrieval.py` — 9 focused tests.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused pytest | `core/.venv/bin/python -m pytest core/tests/knowledge/test_embeddings.py core/tests/knowledge/test_retrieval.py -q` | 22 passed |
| Ruff lint | `core/.venv/bin/ruff check core` | clean |
| Ruff format | `core/.venv/bin/ruff format --check core` | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

Test coverage includes request/response contract, localhost endpoint
inheritance, exact identity, malformed/empty/mixed/dimension/non-finite
vectors, deterministic normalization/cosine/ties, threshold/top-K/dedup,
provenance, smoke tests, index invalidation, transport errors, explicit
unavailable backend, and bounded low-resource limits.

## Security delta

- Strict immutable identities prevent mixing vectors from different embedding
  models/dimensions/normalization; incompatible indexes are never silently
  reused.
- Bounded limits (batch texts, text characters, vector dimensions, retrieval
  top-K) fail before any request or search occurs; no unbounded threads,
  buffers, or admin/accelerator assumptions are introduced.
- Transport failures, unavailable endpoints, malformed responses, and
  cardinality/dimension drift become structured errors; no fake successful
  embedding response exists in product code.

## Residual risk

- LanceDB local embedded backend is not implemented or claimed because the
  dependency is absent. A clean `VectorIndex` protocol and explicit
  `BackendUnavailableError` are provided; the future LanceDB adapter and live
  embedding execution remain pending integration requirements.
- Ollama requests are built in memory with a bounded payload; this is
  acceptable for the configured text/batch limits, but a streaming encoder
  could be considered later for very large batches.
- Real live Ollama `/api/embed` behavior was not exercised by this lane per
  the low-RAM prohibition.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `f1264ce`
  (`feat: add embedding provider and retrieval foundation`) on branch
  `agent/T007-embeddings`, started exactly at base commit `ad685de`.
- This handoff is committed separately on the same branch.
- Merge the four owned source/test files and this handoff through the PM
  integration lane. No lockfile, manifest, existing knowledge file, API, DB,
  or other lane is included.
