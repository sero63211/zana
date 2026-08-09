# T007 Knowledge Canonical Integration Handoff

Verdict: PASS

## Scope and ownership

Transplanted the accepted bounded Knowledge hardening implementation and tests
from the read-only source lane onto the exact canonical base. Only the owned
paths below were changed; no runtime, acquisition, API, DB, domain, manifest,
lockfile, README, UI, observability, streaming, or coordination file was
touched.

- Base commit: `8c655dc0be385b3cf8746eeee9952665a343e821` (canonical master)
- Source lane HEAD: `bcb965386ff5ca38b59bcbe01f1d02bfbaafd657`
  (`agent/T007-knowledge-hardening`, clean before read)
- Accepted source commits (verified as the exact knowledge-path commits):
  `313cb61`, `a36c625`, `b11dffe`, `932e68e`, `01a79da`, `a8a297b`,
  `8dc5252`, `d11691a`, `cf09372`

## Changed paths

Implementation commit `ee388f6` changed:

- `core/zana_core/knowledge/limits.py` (new)
- `core/zana_core/knowledge/models.py`
- `core/zana_core/knowledge/intake.py`
- `core/zana_core/knowledge/normalizers.py`
- `core/zana_core/knowledge/chunker.py`
- `core/zana_core/knowledge/parsers.py`
- `core/zana_core/knowledge/snapshots.py`
- `core/zana_core/knowledge/evidence.py`
- `core/zana_core/knowledge/embeddings.py`
- `core/zana_core/knowledge/retrieval.py`
- `core/tests/knowledge/test_embeddings.py`
- `core/tests/knowledge/test_retrieval.py`
- `core/tests/knowledge/test_hardening.py` (new)
- `core/tests/knowledge/test_chunker.py`
- `core/tests/knowledge/test_evidence.py`
- `core/tests/knowledge/test_normalizers.py`
- `core/tests/knowledge/test_parsers.py`
- `core/tests/knowledge/test_snapshots.py`

This handoff is the second, docs-only commit on the same branch.

## Reconcile checks

The canonical base already contained the pre-hardening knowledge and
embeddings integration plus the canonical Runtime/Acquisition surface. The
accepted source tree was compared path-by-path and copied only for the owned
knowledge paths. Runtime contracts were reconciled against the canonical
`core/zana_core/runtimes/base.py` and `core/zana_core/runtimes/transport.py`
at base HEAD; `runtimes/base.py` and `runtimes/transport.py` are identical
between source and canonical, and every imported symbol
(`HttpResponse`, `HttpTransport`, `InvalidRuntimeResponseError`,
`RuntimeProbeTimeoutError`, `parse_json_object`, `require_http_ok`,
`UrllibTransport`) exists with the same signature in canonical. Knowledge does
not import Acquisition.

## Checks run and evidence

All commands ran in this canonical-based worktree with
`PYTHONPATH=/Users/sero/.codex/worktrees/d2c3/zana/core` so the worktree source
was imported instead of any editable install.

| Check | Command | Result |
| --- | --- | --- |
| Import smoke | `python -c "import zana_core.knowledge.*; import zana_core.main"` | pass |
| Focused knowledge pytest | `python -m pytest core/tests/knowledge -q` | 204 passed |
| Ruff lint | `ruff check --no-cache core/zana_core/knowledge core/tests/knowledge` | clean |
| Ruff format | `ruff format --check --no-cache core/zana_core/knowledge core/tests/knowledge` | clean |
| Pyright knowledge | `pyright core/zana_core/knowledge` | 0 errors, 0 warnings |
| Pyright canonical import smoke | `pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` and `git diff --cached --check` | pass |
| Direct red probes | `_from_plain`/`_from_plain_entries` unsafe calls, `object.__setattr__`, hostile reflected equality, union hostile operand, constructor misuse | all fail closed (ValueError/AttributeError/TypeError/False as accepted) |

The direct probes reproduced the accepted fail-closed behavior without
invoking hostile `__eq__`/`__hash__`/`__iter__`/`__repr__` hooks. Regression
coverage for unsafe plain constructors, tuple-subclass rejection,
`object.__setattr__`, malformed base-tuple wrappers at
repr/equality/copy/union/Pydantic-serializer and embedding/retrieval
boundaries, hostile hooks, aggregate 200+200 union rejection, signed 64-bit
and finite numbers, giant Unicode byte limits, cap+1 iterables, and
filesystem failure paths is preserved in `core/tests/knowledge/test_hardening.py`
and the other focused tests.

No full suite, live sockets, model start/pull/load/inference, downloads,
installs, native builds, sleeps, or large fixtures were run.

## Readability and duplication review

Hard caps and bounded accounting live once in `knowledge/limits.py`; metadata
and durable types live once in `knowledge/models.py`; shared runtime transport
helpers are imported from the canonical runtime boundary. Ruff, Pyright, and a
manual symbol scan found no accidental duplicate helpers or dead
placeholder/TODO code. The only "placeholder" strings are the accepted honest
embedding identity placeholders required by the snapshot contract.

## Security delta

- `FrozenMetadata`/`FrozenMetadataList` are exact `tuple` subclasses with
  `__slots__ = ()` and structurally immutable tuple storage; every
  construction, consumption, equality, repr, copy, union, Pydantic
  serialization, and embedding/retrieval accounting path revalidates the exact
  bounded grammar before traversal.
- Unsafe `_from_plain`/`_from_plain_entries` calls, hostile reflected equality,
  hostile iteration/repr/hash hooks, tuple subclasses, oversized graphs, and
  non-finite/out-of-range numerics fail closed with generic typed errors.
- Intake is dirfd-anchored with `O_NOFOLLOW`/`O_DIRECTORY`, dev/inode identity
  verification, private modes, bounded streaming, and dirfd-only cleanup.
- No secrets, raw error text, network, model, or background execution is
  introduced.

## Residual risk

- An adversarial ancestor swap between root path inspection and the dirfd open
  is minimized by `O_NOFOLLOW|O_DIRECTORY` plus dev/inode verification but is
  not fully eliminated on every platform.
- A future LanceDB adapter and live embedding execution remain pending
  integration requirements; the `Iterable` search protocol and honest
  `BackendUnavailableError` are preserved.
- `ContextPackage` consistency uses the default estimator; a custom estimator
  must reproduce the same totals or the package is rejected.

## Blockers

None.

## Merge instructions

- Branch: `agent/T007-knowledge-canonical-integration`
- Implementation commit: `ee388f6`
  (`feat: integrate canonical bounded knowledge pipeline hardening`)
- Handoff commit: separate docs commit containing this file
- Merge both commits through the PM integration lane. No other path is
  included; do not broad-cherry-pick the source branch.

## Push state

Not pushed. The lead did not request a push; local branch commits are ready for
the lead's fetch/reconcile and non-force push decision.

## Clean proof

After the implementation commit, index and worktree were clean
(`git status --porcelain` empty). The docs commit lands last; final index and
worktree are clean as verified after it.
