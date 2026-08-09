# T007 Streaming Handoff — Canonical Bounded SSE/Event-Stream Boundary

Verdict: PASS

## Scope

Implemented the T007 streaming lane under `core/zana_core/streaming/**` and
`core/tests/streaming/**`, building on the controlling API contract (Jobs SSE
and Chat stream events) without importing jobs, instances, API, DB, or any
existing package. No API wiring, dependency addition, localhost bind,
background work, or shared contract change was introduced.

## Changed files and modules

- `core/zana_core/streaming/models.py` — frozen strict `StreamLimits`,
  `EventCursor`, `StreamEvent`, `EventBatch`, `ErrorMetadata`, `CursorCheck`,
  and `ResumeDecision`; `EventKind` covers generic job progress and chat
  stream names without importing those modules.
- `core/zana_core/streaming/encoder.py` — `SSEEncoder` emits one UTF-8 bytes
  chunk at a time with `id`/`event`/`retry`/`data` framing, deterministic
  compact JSON, multiline `data:` lines, newline/control-character defense
  for names/ids, strict per-data/event/total/retry caps, explicit keepalive
  comments, and raw exception/secret rejection.
- `core/zana_core/streaming/redaction.py` — bounded recursive
  `Redactor`/`redact_value` removing authorization/token/password/secret/
  cookie/API-key values while preserving safe fields under bounded depth,
  items, and string length. Stdlib only.
- `core/zana_core/streaming/source.py` — injected `EventSource` protocol,
  synchronous `drain()` with monotonic cursor ordering, batch/event/byte and
  deadline caps, injected clock/cancellation, honest empty/terminal
  termination, and sanitized source/encode errors. Zero background threads,
  tasks, sleep, polling loops, timers, queues, or telemetry; events are
  emitted through a callback and never accumulated.
- `core/tests/streaming/**` — 50 focused tests.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Streaming tests | `pytest core/tests/streaming -q` | 50 passed |
| Ruff lint | `ruff check core` | clean |
| Ruff format | `ruff format --check core` | clean |
| Pyright | `pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

Test coverage includes exact wire framing, JSON stability, multiline data,
newline injection defense, oversize event/data/total/retry, infinite
generators stopped by caps, cursor resume/stale/invalid/ahead, batch reorder
and source mismatch rejection, cancellation, deadline, empty source,
sanitized source errors, recursive secret redaction, no event accumulation,
and no background creation.

## Security delta

- Raw exceptions/tracebacks and secret-bearing objects are never serialized;
  source failures map to generic typed `ErrorMetadata`.
- Event ids and names reject CR/LF/NUL and other control characters; SSE data
  uses canonical multiline framing.
- Recursive redaction preserves safe fields and replaces sensitive key values
  under bounded depth/items/string length.
- All caps stop before the limit is exceeded and return typed recoverable
  errors/results.

## Residual risk

- Live wakeup, HTTP/API integration, and real job/chat event persistence are
  intentionally deferred to later API integration.
- Keepalives are explicit caller-supplied comments only; no auto-timed
  heartbeat is implemented.
- Cursor monotonicity is enforced per drain; durable replay guarantees depend
  on the future backing store.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `498189a`
  (`feat: add canonical bounded SSE streaming primitives`) on branch
  `agent/T007-streaming`, started exactly from integrated commit `affd50f`.
- This handoff is committed separately on the same branch.
- Merge `core/zana_core/streaming/**`, `core/tests/streaming/**`, and this
  handoff through the PM integration lane. No lockfile, manifest, API, DB, or
  other lane file is included.
