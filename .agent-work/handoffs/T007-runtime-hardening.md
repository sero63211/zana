# T007-runtime-hardening Handoff - Strict Bounded Localhost Probe Registry

Verdict: PASS

## Scope

Implemented the strict bounded hardening of the real localhost probe registry,
starting exactly from integrated commit `e258be4`. Only owned paths were
touched:

- `core/zana_core/runtimes/registry.py`
- `core/zana_core/runtimes/limits.py`
- `core/tests/runtimes/test_registry_hardening.py` (new, owned addition
  beyond the original lane list, documented here)
- `core/tests/runtimes/test_limits.py`
- `.agent-work/handoffs/T007-runtime-hardening.md`

`git diff e258be4..HEAD --stat` confirms `core/tests/runtimes/test_registry.py`
was never modified; the existing registry tests are executed unchanged as
regression coverage only.

Base: integrated commit `e258be4`. Implementation HEAD before this handoff
update: `5549148`. Handoff HEAD: this commit itself (the handoff is the
current branch HEAD after commit). This agent branch was not pushed; the lead
will squash the entire owned delta into one serial integration commit.

No adapter/base/transport/API/DB/manifests/other tests were edited. No
service, LAN scan, subprocess, model call/download, sleep/poll, global pool,
or background survival was introduced.

## Changed files and modules

- `core/zana_core/runtimes/limits.py` - frozen strict `RuntimeProbeLimits`
  (Pydantic v2, `extra="forbid"`): `max_targets <= 16`, `max_workers <= 4`,
  `max_timeout_seconds <= 10`, endpoint/reference length bounds, evidence
  item/char bounds, error char bound, and a cross-field validator requiring
  `max_workers <= max_targets`. `DEFAULT_PROBE_LIMITS` is the canonical
  default.
- `core/zana_core/runtimes/registry.py` - hardened `RuntimeProbeRegistry`:
  - constructor validates `timeout` and `max_workers` against the limits
    before any thread creation; absurd values raise `ValueError`;
  - `probe()` accepts `Sequence` or `Iterable`; every input goes through one
    cap+1 bounded iteration path that stops at `max_targets + 1` without
    materializing the remainder, and `Sequence.__len__` is never trusted for
    safety;
  - empty targets return immediately without creating a `ThreadPoolExecutor`;
    one target or `max_workers == 1` probes synchronously with zero thread
    creation; multiple targets use `min(worker cap, target count)` with a
    scoped executor and all threads joined before return;
  - before scheduling, targets are validated for unique `runtime_id`,
    bounded endpoint/reference strings, per-target timeout, and explicit
    count; duplicates fail deterministically;
  - per-target unexpected failures are isolated and returned as one
    sanitized bounded ERROR descriptor; remaining probes continue; bearer
    tokens, URL credentials, raw exceptions, response bodies, and tracebacks
    are redacted/never exposed;
  - at most `max_targets` futures are submitted; output is sorted stably by
    `runtime_id`;
  - descriptor evidence/warnings/error are bounded (item count and total
    bytes where applicable) before return.
- `core/tests/runtimes/test_registry_hardening.py` - new focused hardening
  tests: invalid constructor values, zero executor for empty input, zero
  threads for single/worker=1, all threads joined after multi-target probes,
  sequence count cap, generator stop at max+1 (consumed == 17), duplicate
  target failure, invalid target failure, unexpected failure isolation with
  sanitized descriptors, stable runtime_id ordering, injected custom limits,
  and bearer/URL-credential redaction. No live sockets or network are used.
- `core/tests/runtimes/test_limits.py` - frozen/extra-forbid/cross-field
  limits tests.
- `core/tests/runtimes/test_registry.py` - existing registry tests were not
  changed; they now pass against the hardened registry (default four-target
  semantics preserved).

## Strict input/output boundary rework (lead gate)

The integration gate required a strict input/output boundary. Reworked the
owned files as follows:

- `RuntimeProbeLimits` gained hard conservative bounds with
  `allow_inf_nan=False` on the timeout: `max_bearer_token_bytes` (4096),
  `max_endpoint_bytes` (4096), `max_reference_bytes` (1024), `max_models`
  (128), `max_model_field_bytes` (256), `max_model_capabilities` (16), and
  `max_models_total_bytes` (256 KiB).
- Registry pre-scheduling validation now enforces:
  - finite positive constructor and per-target timeouts (NaN/`+inf`/`-inf`
    rejected before any executor or transport call);
  - hard UTF-8 byte and control-character bounds for `runtime_id`,
    endpoint, and bearer token (token value is never exposed in errors);
  - endpoint shape: http(s) only, no embedded credentials, no fragment, no
    raw whitespace/backslashes, and a present host, while preserving
    explicit supported local/manual URLs and never scanning the LAN.
- `RuntimeDescriptor.models` is bounded inside registry ownership: model
  count over `max_models` fails the target honestly with a generic bounded
  error descriptor (oversized graph not retained); all model strings are
  truncated to `max_model_field_bytes`, capabilities truncated to
  `max_model_capabilities`, and projected total bytes capped by
  `max_models_total_bytes` (exceeding fails the target honestly).
- `_sanitize_text` inspects only the first `max_error_chars` prefix before
  regex redaction, so regex work and copies are fixed-size regardless of the
  exception string length; credentials/bearer within the retained prefix are
  still redacted.
- `_truncate_text` first slices the untrusted string to at most the byte
  limit in code points (UTF-8 bytes >= code points), then encodes only that
  bounded prefix and trims/decode-safely; the full untrusted string is never
  encoded.
- `bearer_token` validation first rejects `len(codepoints) >
  max_bearer_token_bytes`, then encodes only the bounded candidate to check
  UTF-8 bytes; an arbitrarily long standalone token is never fully encoded
  and its value is never echoed.
- Endpoint validation rejects dangling empty-port syntax (`http://host:`,
  `http://127.0.0.1:`, `http://[::1]:`) generically before scheduling,
  while preserving valid IPv4/hostname/bracketed IPv6 and path/query
  behavior.
- Endpoint port is read exactly once inside `try`; nonnumeric/out-of-range
  ports raise one generic `ValueError("endpoint port is invalid")` without
  echoing the endpoint.
- `_probe_one` and `_bound_descriptor` no longer use `model_copy(update=...)`;
  both rebuild through one validated `_rebuild_descriptor` helper that
  constructs a fresh `RuntimeDescriptor` from bounded fields and applies the
  target identity/endpoint override through validation. No validation
  bypass remains in `registry.py`.
- `ProbeTarget` is validated as an unchecked dataclass: pre-scheduling fails
  closed for `endpoint=None`/non-str, empty `runtime_id`/endpoint, invalid
  `kind`/`source`/`adapter_type` values, and non-numeric/bool/non-finite
  timeouts; `target.timeout is None` is the only "use registry timeout"
  signal, and `0`/`True`/`False`/wrong types are rejected. Constructor
  `timeout`/`max_workers` likewise reject bool/wrong types with stable
  `ValueError` (never `TypeError`) while preserving hard caps.
- `_bounded_strings` stops at a hard item cap during iteration in addition
  to the aggregate character budget; warnings now use the same item cap, so
  arbitrarily many empty strings or warnings cannot materialize a
  millions-sized list. `RuntimeDescriptor.identified_vendor` is bounded
  before final validated reconstruction.
- `_project_model` bounds `ModelDescriptor.runtime_id` and
  `_model_byte_count` includes it in the aggregate byte budget.
- `_sanitize_text` is boundary-conservative: when the retained prefix could
  cut a userinfo/credential token (e.g. `@` beyond `max_error_chars`), the
  prefix is cut at the last safe boundary before regex redaction, so no
  partial user/password/token content is returned at truncation boundaries,
  with fixed work independent of the full error size.
- The boundary detector has no 128-char blind spot: it scans only the
  already-bounded trailing region after the last whitespace for a partial
  `//userinfo` with no `@` inside the retained prefix, and cuts before that
  marker. A URL credential starting near index 0 with a >512-char password
  and the `@` beyond the retained prefix is fully redacted (regression
  covered).
- `_make_adapter` now uses the same explicit `None` timeout semantics as
  validation (`registry.timeout if target.timeout is None else
  target.timeout`), so `0`/`False` per-target values cannot reach an adapter.
- `RuntimeProbeLimits` is now fully strict: every public field uses Pydantic
  `strict=True` so bools, numeric strings, and wrong numeric types
  (e.g. `max_workers=True`, `max_workers="1"`, `max_timeout_seconds=True`,
  `max_timeout_seconds="1.5"`, float for int fields) are rejected before
  use; `max_timeout_seconds` accepts only exact int/float, finite, positive,
  and capped. Cross-validation cannot be bypassed by coercion.
- `_bounded_collect` uses one cap+1 bounded path for every input and never
  trusts `Sequence.__len__` or materializes beyond `max_targets + 1`; a
  hostile Sequence reporting a small length but yielding more than the cap
  is stopped at 17 with no executor/transport reached (regression covered).
- `_sanitize_error` only inspects an already-existing exact `str` argument
  under the prefix cap; hostile/huge `__str__` implementations on
  `RuntimeProbeError` are never invoked and yield the generic bounded
  message. `_bounded_strings` genericizes non-string evidence/warnings to
  `"[non-string]"` instead of calling arbitrary `str()` (regression proves
  zero `__str__` invocations).
- Error isolation is bulletproof: error-descriptor construction no longer
  calls executable discovery; failures in executable discovery or descriptor
  construction yield a small generic bounded descriptor built only from the
  target's already-validated identity/endpoint, and `future.result()` can
  never abort the batch.

New/updated tests cover: NaN/`inf` timeouts with zero transport calls,
bearer/endpoint/reference byte and control bounds, endpoint credentials/
fragment/non-http/malformed rejection, model-count honest failure, bounded
model string/capability projection, total-bytes honest failure, large
exception strings with bounded sanitization and prefix redaction, and one
failing executable provider alongside a successful target, plus fresh
validated descriptor reconstruction and prefix-bounded truncation with
multi-byte emoji boundaries.

## Checks run and evidence

All commands used the existing shared environment at
`/Users/sero/Documents/zana/core/.venv/bin`; no dependencies were installed
and no venv was created.

| Check | Command | Result |
| --- | --- | --- |
| Focused registry/limits tests (fresh) | `core/.venv/bin/python -m pytest core/tests/runtimes/test_registry.py core/tests/runtimes/test_registry_hardening.py core/tests/runtimes/test_limits.py -q` | 62 passed |
| Runtimes suite (fresh) | `core/.venv/bin/python -m pytest core/tests/runtimes -q` (escalated for loopback) | 87 passed |
| Full Core suite (pre-final-rework receipt) | `core/.venv/bin/python -m pytest core/tests -q` (escalated) | 882 passed at `91389ef`; final focused suite/static gates are fresh above |
| Ruff lint | `core/.venv/bin/ruff check` on owned files | clean |
| Ruff format | `core/.venv/bin/ruff format --check` on owned files | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core/runtimes/registry.py core/zana_core/runtimes/limits.py` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

## Security delta

- Hard maxima and cross-field validation prevent unbounded target counts,
  worker counts, timeouts, endpoint/reference strings, and evidence growth
  before any thread is created.
- Bounded iterable collection prevents millions-sized lists/futures dicts.
- Per-target failures never abort other probes and never expose bearer
  tokens, URL credentials, raw exception strings, response bodies, or
  tracebacks; URL userinfo, bearer values, and labeled token/secret/api-key
  fragments are regex-redacted within the bounded retained prefix.
- Every configurable maximum is truly hard: each `RuntimeProbeLimits` `le`
  equals its named `MAX_*` constant, so raising any cap is rejected by model
  validation.
- Model textual fields and aggregate output are bounded by UTF-8 bytes with
  a truncator that never splits code points; final `RuntimeDescriptor` and
  `ModelDescriptor` values are validated through fresh construction, never
  copy-bypass.
- Zero executor for empty/single/worker=1 paths and scoped executor shutdown
  ensure no background thread survival or global pool.

## Residual risk

- The existing transport bound of 1 MiB per response remains in the
  unchanged `UrllibTransport` (out of ownership); registry-level evidence/
  error bounds apply to descriptors returned by adapters.
- Response/model list parsing inside adapters is unchanged; only the
  registry layer is hardened.
- Default four-target localhost semantics are preserved; constructor
  rejection of out-of-range timeouts/workers is a deliberate fail-closed
  behavior change.
- The prior 882-pass full Core receipt predates the final `4187c96` rework;
  only the focused suite and static gates are claimed fresh for the final
  delta, and the runtimes suite was rerun fresh (87 passed).

## Blockers

None.

## Commit and merge instructions

Complete owned commit history on `agent/T007-runtime-hardening`:

- `62a88f9` feat: harden runtime probe registry limits
- `ac073a5` docs: add T007-runtime-hardening handoff
- `9b8f8a4` fix: enforce strict runtime probe input and output bounds
- `39ba34a` docs: update T007-runtime-hardening handoff with strict boundaries
- `91389ef` fix: hard-bind probe caps and bound model output by bytes
- `3e95af2` docs: update runtime hardening handoff with byte-bound rework
- `4187c96` fix: validate reconstructed descriptors and bound truncation prefix
- `169e7f3` fix: bound bearer encode and reject dangling endpoint ports
- `157abbd` fix: close target validation and bounded output gaps
- `fa4199b` fix: close credential boundary blind spot and adapter timeout semantics
- `5549148` fix: enforce strict limits and bounded target collection
- `5549148` is followed by this handoff update commit (current branch HEAD).

The lead will squash the entire owned delta (all commits above) into one
serial integration commit and perform the safe fetch/reconcile/push/receipt
cycle. No lockfiles, DB schema, API registration, desktop files, or GoalBuddy
state are included. After integration, rerun the focused runtimes suite and
the full Core suite with loopback permission.

Clean-tree proof: `git status --short --branch` reports only
`## agent/T007-runtime-hardening` after the final handoff commit.
