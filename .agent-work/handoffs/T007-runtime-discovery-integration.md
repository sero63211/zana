# T007-runtime-discovery-integration Handoff

Verdict: PASS

## Scope

Closed the direct-audit red gates on the integrated runtime discovery
registry.  Canonical HEAD `8c655dcbe385b3cf8746eeee9952665a343e821`, clean
detached worktree, and the reference branch `agent/T007-runtime-hardening` at
`5d8c96f` were verified first.  The owned files were manually reconciled and
hardened further; no broad merge or cherry-pick was used.

Actual changed paths across the two corrective work sessions:

- `core/zana_core/runtimes/registry.py`
- `core/zana_core/runtimes/limits.py`
- `core/tests/runtimes/test_registry_hardening.py`
- `.agent-work/handoffs/T007-runtime-discovery-integration.md`

`core/tests/runtimes/test_limits.py` was never modified.  No adapter, base,
transport, API, DB, manifest, state.yaml, ledger, or other test file was
modified.  No runtime, model, service, app, download, full build, or live
network probe was started.

## Changed behavior

- `RuntimeProbeRegistry.__init__` uses explicit `None` checks for
  `limits`, `transport`, and `executables`; hostile truthiness is never
  invoked.  Limits are exact-revalidated field-by-field into a fresh
  instance, and the canonical default is built fresh rather than retained
  from a mutable global.
- Constructor and per-target timeouts/workers require exact builtin numeric
  types before math/comparison; `EvilInt`/`EvilFloat` subclasses and bools
  are rejected.
- `ProbeTarget` must be an exact instance; every field is exact-read from the
  raw `__dict__` and validated before any hash/equality/urlsplit/encode.
  `runtime_id` is bounded before duplicate-set insertion, and caller values
  are never interpolated into error strings.
- Endpoints are now loopback-only: `localhost`, IPv4/IPv6 loopback via
  `ipaddress`, no embedded credentials, no fragments, no DNS/LAN tricks.
  The entire batch is validated before any transport call.
- One monotonic batch deadline is established in `probe()`; queued targets
  receive only bounded remaining time, and work after expiry never calls the
  transport.  Executor cleanup is scoped and all threads are joined before
  return.
- Adapter `RuntimeDescriptor`/`ModelDescriptor` output is untrusted:
  exact model types and raw namespaces are required, all fields are
  exact-revalidated (str/enum/bool/int/datetime/list) before
  len/slice/encode/sort, timestamps must be the exact `UTC` singleton, model
  numerics are nonnegative and capped, and capabilities are length-gated
  before traversal.  Outputs are rebuilt as fresh validated objects.
- Evidence and warnings are treated as untrusted display text: only exact
  strings are retained, the prefix is gated before encode/regex work, URL
  credentials and token/secret/password/api-key patterns are redacted, and
  control characters are neutralized.
- Every retained descriptor/model text field is exact, byte-bounded, and
  control-free before rebuilding; invalid `identified_vendor`, model
  identifiers, display names, and metadata strings fail the target into the
  generic bounded error descriptor without hostile hooks.
- Projected models are bound to the validated target snapshot: the raw model
  `runtime_id` is exact-validated first, then every projected model carries
  the snapshot `runtime_id`, so a cross-runtime model is never surfaced under
  a foreign id.
- Endpoint queries are rejected (credentials/tokens cannot be smuggled in a
  query), while bounded loopback paths such as `/v1/models` remain accepted.
- `_sanitize_error` reads `args` through the exact `BaseException` descriptor
  and accepts only an exact one-element tuple of exact `str`;
  `_sanitize_text` rejects non-exact strings before slicing/regex.
- Error fallback descriptors use only the trusted `_TargetSnapshot` built
  during batch validation.
- `ExecutableDiscovery.installed` must return an exact bool, and falsy
  injected transports cannot fall back to the real `UrllibTransport`.
- `_trusted_runtime_config` reads raw registry state and fails with a typed
  `ValueError` when `limits`, `timeout`, or `max_workers` is deleted or
  corrupted; no raw `AttributeError` or hostile attribute hook can escape.

## Checks run and evidence

All commands used the existing shared venv
`/Users/sero/Documents/zana/core/.venv/bin`; no dependencies were installed.

| Check | Command | Result |
| --- | --- | --- |
| Focused pytest | `PYTHONPATH=<worktree>/core .venv/bin/pytest -q core/tests/runtimes/test_registry.py core/tests/runtimes/test_registry_hardening.py core/tests/runtimes/test_limits.py` | 95 passed |
| Ruff lint | `.venv/bin/ruff check` on owned files | clean |
| Ruff format | `.venv/bin/ruff format --check` on owned files | clean |
| Pyright | `.venv/bin/pyright core/zana_core/runtimes/registry.py core/zana_core/runtimes/limits.py` | 0 errors, 0 warnings |
| Import smoke | `PYTHONPATH=core .venv/bin/python -c "import zana_core.runtimes.registry; import zana_core.runtimes.limits"` | pass |
| Diff hygiene | `git diff --check` | pass |

## Security delta

- Hard exact-type bounds before every comparison, encode, hash, sort, and
  traversal; bool/subclass/hostile-hook inputs fail closed.
- Loopback-only endpoint policy eliminates remote/LAN probing and DNS tricks.
- Shared batch deadline and bounded executor submission leave no post-deadline
  transport calls or background threads.
- Credentials, bearer values, raw exceptions, response bodies, tracebacks,
  evidence/warning secrets, and hostile `__str__`/`__repr__`/
  `__getattribute__` output are never exposed; errors are generic bounded
  messages.
- Corrupted `RuntimeProbeLimits`/descriptor/model objects built through
  `model_construct` or `object.__setattr__` fail safely; mutable defaults are
  never retained.

## Residual risk

- Adapter response parsing and the existing 1 MiB transport cap remain
  outside this owned scope; registry-level validation and bounds now apply to
  every descriptor the adapters return.
- Live loopback/runtime behavior was intentionally not probed per task
  constraints; verification used injected fake transports only.
- Pyright was run on the two owned implementation modules, matching the
  prior runtime-hardening gate; owned tests intentionally exercise invalid
  static types at runtime and are excluded from that gate.

## Blockers

None.

## Commit and merge instructions

The lead integrator must squash/merge only the four actual owned paths
(`registry.py`, `limits.py`, `test_registry_hardening.py`, and this handoff)
into the canonical `origin/main` integration lane, preserve the public
registry API and existing adapter consumers, and perform the safe
fetch/reconcile/non-force push cycle.  This task does not push; the explicit
push blocker is lead-integration only.

Clean-tree proof after commits: `git status --porcelain` is empty and
`git diff --check` passes.

## Canonical integration receipt

- accepted canonical commit: `b86263764988f8af2c6887f382bc3c2b43b03087`
- canonical branch: `master`, tracking `origin/main`
- changed product paths: `core/zana_core/runtimes/registry.py`,
  `core/zana_core/runtimes/limits.py`, and
  `core/tests/runtimes/test_registry_hardening.py`
- post-integration gates: 91 focused hardening/limits tests passed; the full
  Runtime suite passed with 120 tests; Ruff check and format passed; Pyright
  reported 0 errors and 0 warnings; cached diff hygiene passed
- security delta: loopback-only bounded discovery now rejects query/credential
  smuggling, sanitizes untrusted evidence and warnings, binds projected models
  to the validated runtime identity, and fails closed on corrupted registry
  configuration
- residual risk: live runtime/model discovery remains intentionally unexecuted
  until the resource-capped end-to-end acceptance phase
- push proof: non-force `master:main` push succeeded and `origin/main` was
  confirmed at `b86263764988f8af2c6887f382bc3c2b43b03087`
- clean proof before receipt staging: index and worktree diffs both exited 0
