# T007 Acquisition Handoff — Native Runtime Acquisition Boundary

Verdict: PASS (seven rework rounds applied after lead gate)

## Scope

Implemented a dependency-free native runtime acquisition boundary under
`core/zana_core/acquisition/**` and focused tests under
`core/tests/acquisition/**`. No runtimes, resources, API, DB/domain/jobs, root
manifests/lockfiles, desktop, GoalBuddy state, ledger, or other package was
edited. No model/runtime was started, no network/download occurred, and no
localhost bind or large fixture was used.

## Changed files and modules

- `core/zana_core/acquisition/models.py` — frozen typed request/plan/progress/
  result/failure/cancellation models, bounded model-reference validation with
  control-byte rejection, and actionable unsupported-runtime result.
- `core/zana_core/acquisition/limits.py` — hard caps for line bytes, total
  event bytes, event count, retained events, model-reference bytes,
  concurrency, and deadline.
- `core/zana_core/acquisition/endpoints.py` — explicit http(s), loopback,
  credential/fragment, and remote-policy validation; no network scanning.
- `core/zana_core/acquisition/admission.py` — narrow injected admission
  protocol; unknown expected size/headroom requires explicit approval plus a
  conservative reserve and never allows by guess.
- `core/zana_core/acquisition/protocols.py` — injected streaming transport,
  admission provider, cancellation token, and conservative lock protocols.
- `core/zana_core/acquisition/ollama.py` — real Ollama native adapter posting
  `/api/pull` with `stream=true`, consuming bounded JSONL progress metadata
  only. The bounded incremental `_JsonlFramer` is now lazy: lines are yielded
  one at a time so the event cap stops scanning without prebuilding or
  processing the rest of a chunk. It scans newlines before enforcing per-line
  caps, handles split and multi-line chunks, caps the unfinished tail,
  enforces total raw bytes before growth, and parses the tail at EOF.
  Deadline/cancellation/event-count checks run before every inner line
  construction/retention so a multi-line chunk cannot bypass timing. Only
  capped retained history exists; no unbounded event collection.
- `core/zana_core/acquisition/limits.py` — conservative hard upper bounds on
  every resource dimension plus consistency validation
  (`retained <= event_count`, `line <= total`); the unused
  `default_deadline_seconds` knob is removed because the request deadline is
  canonical. `MAX_PROGRESS_VALUE` and `MAX_ADMISSION_HEADROOM` cap resource
  settings at a conservative 1 TiB absolute bound.
- `core/zana_core/acquisition/admission.py` — canonical bounded denial reason
  codes; provider reasons are never returned to product results.
  `AdmissionConfig` is a frozen validated policy with hard reserve/headroom
  bounds.
- `core/zana_core/acquisition/models.py` — complete public hard caps: progress
  total/completed/sequence, result events_consumed/error_code/retained
  history, plan endpoint/path/model/body, and typed frozen
  `OllamaPullBody` with cross-field plan/body consistency.
- `core/zana_core/acquisition/service.py` — default service now owns a tiny
  thread-safe `threading.BoundedSemaphore` wrapper enforcing
  `max_concurrent_acquisitions` with non-blocking acquire; an external
  injected lock can replace it. The service computes one monotonic absolute
  deadline at `acquire` entry, passes it and the clock to the adapter, and
  never resets it through validation/admission/plan/body/transport.
- `core/zana_core/acquisition/endpoints.py` — endpoints are normalized to an
  origin only; path/query/fragment, invalid numeric ports, and over-2000-byte
  endpoints are rejected before any admission/transport.
- `core/zana_core/acquisition/service.py` — the validated normalized origin is
  now propagated into the plan/request/result consistently, so a trailing `/`
  cannot produce `...//api/pull`; the normalized request is used for the
  model-reference byte check and the adapter.
- `core/zana_core/acquisition/ollama.py` — huge or inconsistent progress
  integers are bounded before any arithmetic; invalid numeric metadata is
  dropped or fails as a canonical malformed event, never raw big-int
  arithmetic or TRANSPORT_FAILED.
- `core/zana_core/acquisition/models.py` — shared bounded UTF-8 validators
  enforce char and byte limits on progress status/digest/error, admission
  reason, error strings, unsupported result fields/actions, and plan literals.
  Plan invariants are exact typed literals: `kind: Literal[OLLAMA_PULL]`,
  `method: Literal["POST"]`, `path: Literal["/api/pull"]`,
  `stream: Literal[True]`, and `OllamaPullBody.stream: Literal[True]`, with
  the body/model cross-field validator retained.
- `core/zana_core/acquisition/admission.py` — the frozen `AdmissionConfig` is
  retained as the sole effective policy; `reserve_bytes`/`headroom_unknown`/
  `headroom_bytes` are read-only properties backed by `_config`, and neither
  the config binding nor derived properties can be reassigned.
- `core/zana_core/acquisition/service.py` — release happens exactly once only
  after a successful acquire; semaphore over-release is not suppressed, and
  injected cleanup/lock failures raise one canonical `AcquisitionReleaseError`
  raised `from None` with no request/endpoint/model or raw exception chain
  leakage. Normalized requests are
  re-validated by constructing a fresh `NativeAcquisitionRequest` instead of
  `model_copy`, and admission plus the adapter receive the same normalized
  request.
- `core/zana_core/acquisition/endpoints.py` — char budget is checked before
  bounded UTF-8 encoding, `parsed.port` is caught, and
  nonnumeric/out-of-range ports raise one generic `EndpointError` without
  echoing the endpoint; the duplicate service-side numeric-port helper is
  removed. Dangling port delimiters are rejected generically, and the
  normalized origin is reconstructed from validated scheme/hostname/port
  (with bracketed IPv6) rather than raw netloc, so malformed delimiters,
  casing, and userinfo cannot survive.
- `core/zana_core/acquisition/ollama.py` — network projection uses a
  deterministic byte-safe truncator for status/digest/error matching the
  public byte budgets, never splits a code point, and never mislabels
  oversized emoji as transport failure.
- `core/tests/acquisition/**` — 83 focused tests using injected transports
  only; overlapping concurrency tests use real threads with a blocking
  injected transport and an event barrier.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused pytest | `core/.venv/bin/python -m pytest core/tests/acquisition -q` | 83 passed |
| Ruff lint | `core/.venv/bin/ruff check core` | clean |
| Ruff format | `core/.venv/bin/ruff format --check core` | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

Test coverage includes chunked JSONL, split/multi-line framing, a chunk larger
than the line cap containing many valid short lines, split tails across
chunks, malformed/oversized/unterminated lines, a >2000-line chunk stopping at
the event cap without parsing later malformed data, timeout propagation and
one injected absolute deadline through cumulative phases, deadline exhaustion
before open, cancel, disk denial/unknown size, unknown headroom despite
approval, remote-policy denial, single admission invocation, real overlapping
thread concurrency with release after success and exception, bounded
retention, absurd-policy rejection and consistency validation, actual strict
increasing sequence, UTF-8 byte overflow, secret-bearing error sanitization
and canonical bounded denial codes, close-after-open-failure without masking
the typed result, origin-only endpoint rejection with single-slash URL
assertion, invalid-port rejection before admission/transport, huge and
completed>total progress integers producing canonical malformed/drop results,
emoji byte-boundary truncation/redaction, nonnumeric-port generic error,
deadline exhausted by normalization+admission before open, exact typed plan
literals, immutable admission config binding, exact-once release plus
canonical no-leak release failure after success and transport failure,
dangling-port rejection, reconstructed IPv4/IPv6 origins, direct model
construction rejecting oversized lists/strings/numbers/reserves, and
no-byte-proxy semantics.

## Security and lightweight delta

- Only explicit endpoints are accepted; local loopback by default and remote
  only under an explicit policy. Credentials in URLs, fragments, non-http(s),
  and network scanning are rejected.
- Streaming is incremental with hard line/event/total-byte caps and bounded
  retention; no unbounded buffers, histories, background threads, processes,
  timers, poll loops, or sleep.
- Cooperative cancellation is checked between streamed events and closes the
  transport immediately; stalled/malformed/oversized streams fail explicitly.
- One monotonic absolute deadline starts at `AcquisitionService.acquire` and
  is never reset; only remaining time is passed to `open_stream`, and the
  adapter fails before open if the deadline is already exhausted.
- Endpoint credentials/tokens and raw native error bodies are never exposed;
  network-provided status/digest/error strings are bounded and native errors
  are redacted to a generic typed failure. No model bytes, secrets,
  telemetry, or progress payload dumps are logged.
- Unknown expected size proceeds only with explicit approval AND known
  headroom >= the conservative reserve; unknown headroom always blocks even
  with approval.
- Unsupported runtimes return actionable native instructions plus
  refresh-discovery action, never fake success.

## Residual risk

- The streaming transport is an injected protocol; the future real
  stdlib/httpx streaming adapter and API/job wiring remain for an integration
  lane.
- Live Ollama `/api/pull` behavior was not exercised per the no-network
  prohibition.
- The real transport must honor the propagated timeout value when wired; the
  adapter-side injected clock already enforces the absolute deadline.

## Blockers

None.

## Commit and merge instructions

- Implementation commits, in order:
  - `ee11b36` (`feat: add native acquisition boundary`)
  - `dd22b70` (`fix: enforce bounded framer, single admission, deadline, and
    redaction`)
  - `e9af685` (`fix: harden JSONL framing, caps, deadlines, and denial
    redaction`)
  - `86cb417` (`fix: enforce default concurrency and complete model caps`)
  - `6e2d7a3` (`fix: make framing lazy and enforce concurrency, caps, and
    deadline`)
  - `1985a7f` (`fix: normalize origins, bound UTF-8 fields, harden progress
    math`)
  - `6eb5b57` (`fix: exact plan literals, revalidated origins, canonical
    release failure`)
  - `85042b6` (`fix: canonical no-leak release error and origin
    reconstruction`)
  on branch `agent/T007-acquisition`, started exactly at base commit
  `045b0e9`.
- This handoff is committed separately on the same branch.
- Merge `core/zana_core/acquisition/**`, `core/tests/acquisition/**`, and this
  handoff through the PM integration lane. No lockfile, manifest, API, DB,
  runtime, or other lane is included.
