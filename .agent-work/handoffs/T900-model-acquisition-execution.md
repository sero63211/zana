# T900 Model Acquisition Execution Handoff

Verdict: PASS

## Changed files and modules

- `core/zana_core/acquisition/**`: strict native pull request, disk admission,
  bounded progress parsing, cancellation, sanitization, transport, and worker
  supervision.
- `core/zana_core/jobs/model_pull.py`: durable queue-to-terminal execution,
  short transactions, progress persistence, restart recovery, and exact
  post-pull discovery confirmation.
- `core/zana_core/runtimes/discovery_service.py`: bounded cap+1 target snapshot,
  probe outside every DB unit of work, and atomic sync-plus-success persistence.
- `core/zana_core/api/{models,jobs,runtimes}.py` and `core/zana_core/main.py`:
  authenticated pull/cancel/refresh wiring and deterministic shutdown cleanup.
- Focused acquisition, job, runtime, and API fixture tests.

## Checks run and evidence

- Lead: 93 critical fake/unit tests PASS in the source worktree and the same 93
  PASS after canonical integration.
- Worker: owned-surface selectors reached 301 focused PASS; final atomicity
  selector set reported 296 PASS.
- Ruff check and format check PASS.
- Pyright on owned implementation sources: 0 errors, 0 warnings.
- Import smoke and `git diff --check`: PASS.
- Direct multi-pass lifecycle, transaction, error-path, and secret-disclosure
  inspection: PASS.
- No live runtime, network, model, download, server, browser, app, broad suite,
  build, performance, or load test ran.

## Security delta

- Exact loopback-only `POST /api/pull` with fixed JSON body and headers, bounded
  per-I/O timeout, generation-safe in-flight invalidation, and no raw transport
  exception disclosure.
- Explicit user approval, exact ONLINE runtime identity, conservative model
  reference grammar, fail-closed disk/lease admission, and bounded queue/event/
  target persistence.
- Cancellation-safe supervisor and transport cleanup; restart/shutdown failures
  are durable and sanitized.
- No DB transaction spans injected network work; discovery sync and SUCCEEDED
  state commit atomically or roll back together.

## Residual risk

- Real Ollama/urllib interoperability and cancellation timing are unverified.
- Live local-model download, runtime, API server, desktop/browser, bundle,
  broad-suite, performance, and load verification remain intentionally deferred
  under the host-safety policy.

## Blockers

None for the code-complete fixture-tested milestone.

## Merge instructions and receipt

- Source branch: `agent/t900-model-acquisition-execution`.
- Source product commits: `1ffce76`, `339f56f`, `2a87c1c`, `efe4b02`.
- Canonical commits: `137ee6a`, `fdaa5aa`, `9e11e64`, `dbad578`.
- Accepted canonical product head: `dbad57831579d50c77448e825d1105fd8caa7831`.
- Remote: non-force push confirmed; `git ls-remote` resolved
  `refs/heads/main` to the accepted product head before this receipt update.
- Clean proof: canonical index/worktree and the source worktree were clean; no
  out-of-ownership shared repository edit remains.
