# T007 Observability Integration Retry Handoff

Verdict: PASS

## Lead acceptance typing gate follow-up (clean HEAD `e28907e`)

Independent lead acceptance reproduced one red Pyright gate in
`core/zana_core/observability/events.py`: `_FrozenDict.__getitem__` overrode
`tuple.__getitem__` incompatibly with `key: str | int`. Repaired in commit
`5acf182` (owned paths only):

- `_FrozenDict.__getitem__` now accepts `key: Any` so the override is
  compatible with tuple's `SupportsIndex`/slice base overloads, while runtime
  behavior is unchanged: exact `str` keys use the immutable mapping lookup,
  exact `int`/`slice` delegate to tuple indexing, and everything else raises
  `TypeError` without hostile hook dispatch. No validation, immutability, or
  tuple behavior was weakened; no broad abstraction was introduced.
- Focused regression covers string-key lookup, integer tuple indexing, slice
  indexing, and rejection of bool indexing.

Verification: exact Pyright command `pyright
core/zana_core/observability core/zana_core/streaming/redaction.py` reports
0 errors/0 warnings; focused streaming + observability suite 226 passed; Ruff
check and format clean; `git diff --check` clean; clean index/worktree after
the corrective commit. No push/install/live model/full suite run.

## Final sink-result injection audit follow-up (clean HEAD `1beced0`)

## Final sink-result injection audit follow-up (clean HEAD `1beced0`)

The final direct sink-result gate is closed in commit `c820b8e` (owned paths
only, no broad changes, no prior commit rewritten):

- `_safe_event_id` now requires an exact `str` operation_id that is at most
  128 characters, within a 512 UTF-8 byte cap, and free of C0/DEL control
  characters. Character length is gated before encoding, so work is bounded;
  corrupted values are never sanitized or echoed, and missing/corrupt state
  returns `""` fail-closed.
- Regressions cover newline, CR, tab, NUL, DEL, oversized Unicode IDs, and
  valid ID preservation across `BoundedMemorySink`, `LocalJsonlSink`,
  `CompositeSink`, and `TelemetryDisabledSink`.

Verification: focused streaming + observability suite 225 passed, Ruff check
clean, Ruff format clean, Pyright owned source 0 errors/0 warnings,
`git diff --check` clean, clean index/worktree after the corrective commit.
No push/install/live model/full suite run.

## Reopen audit follow-up (clean HEAD `e9312a0`)

## Reopen audit follow-up (clean HEAD `e9312a0`)

Lead direct audit found two remaining red failure-path gates; both are
repaired in commit `6cb1101` (owned paths only, no prior commit rewritten):

1. Exact `datetime` with a hostile `tzinfo` no longer runs any timezone method
   hook. `Event._utc_timestamp` and `_require_utc_timestamp` now require the
   exact trusted `datetime.UTC` singleton identity via `value.tzinfo is UTC`
   and only then call `isoformat()`; `utcoffset`/`dst`/`tzname` are never
   invoked. Regressions cover construction (`ValidationError`), `redact_event`,
   `serialize_event` fixed dropped record, and memory/JSONL/composite/disabled
   sink writes, with a hostile tzinfo hook counter asserted at zero.
2. `object.__delattr__(event, "operation_id")` no longer escapes sinks.
   `_safe_event_id` is now total/fail-closed: exact `Event` check, base
   `object.__getattribute__` access in a narrow exception boundary, exact
   bounded safe `str`, and `""` on missing/corrupt state without hostile
   hooks. Regressions prove memory/JSONL/composite/disabled sinks return a
   typed `WriteResult` with empty `event_id` and never raise, while corrupt
   JSONL writes emit only the fixed dropped record.

Verification for the reopened gate run: focused streaming + observability
suite 222 passed, Ruff check clean, Ruff format clean, Pyright owned source
0 errors/0 warnings, `git diff --check` clean, clean index/worktree after the
corrective commit. No push/install/live model/full suite run.

## Blocking audit follow-up (canonical base `8c655dc`)

## Blocking audit follow-up (canonical base `8c655dc`)

Lead reopened the integrated intermediate code before commit/PASS and
identified the following red gates; each is repaired and regression-tested:

1. `_redact_mapping` now spends the shared item budget for every encountered
   mapping entry, including non-string, reserved-marker, and oversized keys,
   so a huge exact dict of invalid keys stops at `max_items` instead of
   traversing the full source map.
2. `_trusted_limits` revalidates every `RedactionLimits` field into a fresh
   trusted immutable instance at every public consumption
   (`truncate_safe_string`, `Redactor.__init__`, `redact_value`) using
   `object.__getattribute__`; `model_construct`/`object.__setattr__` corruption
   and hostile hooks fail closed, and `Redactor` never retains an externally
   mutable reference.
3. Payload integers in `observability/events.py` are gated to signed 64-bit
   before `str`/`json` conversion, in both validation and trusted conversion;
   the canonical redactor applies the same exact integer bound before `str`.
4. `redact_event` now rechecks every declared Event/EventContext field range
   after `object.__setattr__`/`model_construct`, including
   `schema_version`, `progress_0_1`, `duration_ms`, message UTF-8 bytes,
   identifier bounds/control chars, enum exactness, timestamp, and payload
   grammar; corrupted exact Events produce the fixed dropped record instead of
   serializing invalid values.
5. Exact corrupted/base-constructed `RedactionLimits`, `Event`, `EventContext`,
   and `_FrozenDict` are exercised at redact/serialize/sink consumption with
   hostile comparison/repr/hash/index counters asserted at zero; serialize
   emits only the fixed dropped record.
6. Path handling in `_safe_path_value` gates character length before any
   encoding and computes the digest from a bounded prefix plus exact length,
   never encoding or scanning the entire oversized caller string; the
   regression monkeypatches the digest entry point and proves only bounded
   prefix work for a 10M-character path value.

Hardening commit: `cad9b5b` (owned paths only). Gate evidence for the follow-up
run: focused owned suite 216 passed, Ruff check/format clean, Pyright source
0 errors/0 warnings, `git diff --check` clean, clean index/worktree after the
hardening commit. Pyright on test files remains the accepted adversarial
negative-test typing set only.

## Original scope

## Preflight

- Worktree: `/Users/sero/.codex/worktrees/7fac/zana`
- Branch: `agent/T007-observability-integration-retry`
- Base: canonical `8c655dc0be385b3cf8746eeee9952665a343e821` (verified before
  writing; detached clean HEAD matched the required base)
- Remote: `origin https://github.com/sero63211/zana.git`
- Preflight proof: repo root, HEAD, branch, remote, and empty
  `git status --porcelain` verified before any edit; no other writer shares
  this worktree.
- Source reference: accepted clean worktree
  `/Users/sero/.codex/worktrees/4104/zana` at
  `ce9e8e239e1e2336d17e374b7be04f94d94b8b68`. The final accepted observability
  implementation was manually reconciled (no broad merge, no
  `cherry-pick -X theirs`) onto the canonical streaming boundary.

## Changed files and modules

- `core/zana_core/observability/__init__.py` — local-only structured event API
- `core/zana_core/observability/events.py` — strict frozen bounded Event models
- `core/zana_core/observability/redact.py` — exact-Event redaction adapter
- `core/zana_core/observability/serialization.py` — bounded JSON Lines codec
- `core/zana_core/observability/sinks.py` — memory/JSONL/composite sinks
- `core/zana_core/streaming/redaction.py` — canonical single bounded redactor
- `core/tests/observability/__init__.py`, `test_events.py`, `test_redact.py`,
  `test_serialization.py`, `test_sinks.py`
- `core/tests/streaming/test_redaction.py`
- `.agent-work/handoffs/T007-observability-integration-retry.md`

Canonical streaming API preservation: every canonical name remains importable
from `zana_core.streaming.redaction` (`RedactionLimits`,
`DEFAULT_REDACTION_LIMITS`, `RedactionProvider`, `Redactor`, `redact_value`,
`is_sensitive_key`, `REDACTED`, `TRUNCATED_SUFFIX`, `truncate_safe_string`).
`truncate_safe_string` accepts the accepted observability
`RedactionLimits | None` bounds and preserves the canonical integer
`max_length` API through an exact-type compatibility overload; the two bound
sources are mutually exclusive and both exact-type checked. No canonical
consumer used the old integer signature, and `streaming/__init__.py` /
`encoder.py` import contracts are untouched.

## Checks run and evidence

Shared existing Core venv at `/Users/sero/Documents/zana/core/.venv`; nothing
installed, no model/service start, no app launch, no package install, no full
build, no heavy test.

| Check | Command | Result |
| --- | --- | --- |
| Focused suite | `PYTHONPATH=/Users/sero/.codex/worktrees/7fac/zana/core /Users/sero/Documents/zana/core/.venv/bin/pytest -q core/tests/streaming core/tests/observability` | 189 passed |
| Ruff check | `ruff check zana_core/observability zana_core/streaming/redaction.py tests/observability tests/streaming/test_redaction.py` | clean |
| Ruff format | `ruff format --check ...` | 12 files already formatted |
| Pyright source | `pyright zana_core/observability zana_core/streaming/redaction.py` | 0 errors, 0 warnings |
| Import smoke | `import zana_core.streaming, zana_core.observability` plus all canonical redaction names | pass |
| Diff hygiene | `git diff --check` | pass |
| Clean proof | `git status --porcelain` after implementation commit | empty |

Pyright note: the project config (`include = ["zana_core"]`) gates source only.
Owned source is 0/0. The accepted adversarial tests intentionally pass invalid
runtime values (hostile objects, coercible literals) and are not part of the
configured Pyright include; the same accepted reference files produce the same
negative-test typing findings under an explicit test-file Pyright invocation.

## Invariant coverage

- Bounded exact-type conversion: `_FrozenDict` key/string/UTF-8, map/list,
  depth, duplicate-key, aggregate-byte, and cycle/alias bounds are enforced
  before retention and again before every trusted materialization.
- Invalid/base-constructor conversion: `tuple.__new__(_FrozenDict, ...)`
  bypass instances fail closed via `_frozen_grammar_bytes` before any
  recursive work.
- Hostile hooks: exact-type checks only; `__eq__`, `__hash__`, `__repr__`,
  `model_dump`, mapping/iterable hooks on hostile values are never invoked;
  invalid equality operands return `False`.
- Valid nested redacted values compare `True` through `Event.payload`.
- Integer subclasses and short writes: exact `type(x) is int` guards in Event
  fields, sink bounds, and `_short_write`; bools/subclasses/over-reporting are
  rejected.
- Root rename/symlink races: `LocalJsonlSink` anchors a held dirfd, rechecks
  dev+ino on every operation, uses `O_NOFOLLOW`/`O_CLOEXEC` and dir-relative
  calls, and never writes outside the approved root.
- No model downloads, model execution, app launch, package install, full
  build, or heavy test was run.

## Security delta

Zero new dependencies; telemetry remains disabled with no network endpoint,
background thread, queue, or poll loop. Secrets/content/private paths cannot
reach JSONL output or memory snapshots; snapshots and file lines are canonical
redacted records only. Hostile objects are never introspected, serialized, or
reflected in errors; fallback records and error codes are fixed and bounded.
File confinement uses real non-symlink root checks, anchored directory fds,
no-follow opens, fstat identity checks, restrictive `0600` mode, short-write
loops, fsync, and dir fsync. Rotation uses `os.replace` only with best-effort
rollback.

## Residual risk

- Synchronous `fsync` per write/rotation is intentionally truthful but has
  measurable I/O cost.
- Rotation rollback is best-effort; failed rollback reports
  `ROTATION_UNCERTAIN` and increments `failures`.
- `truncate_safe_string` legacy integer calls now route through
  `RedactionLimits`, so oversized values reject (`gt=0`/`le=2048`) instead of
  silently truncating; no canonical consumer used that path.

## Blockers

None.

## Merge instructions

Integrate branch `agent/T007-observability-integration-retry` onto the PM
integration branch without broad merge; this branch is a single linear child
of canonical `8c655dc`. Cherry-pick or fast-forward the focused implementation
commit and the receipt handoff commit in order. No lockfile, manifest, schema,
migration, API registration, GoalBuddy state, ledger, or other-lane path is
included.

## Accepted commit

- Implementation commit: `e8080f1` (focused; owned paths only)
- Receipt handoff commit: follows this file update (receipt-only)
- Clean index/worktree proof after implementation commit: `git status
  --porcelain` empty; re-proven after receipt commit below

## Remote state and push blocker

Not pushed. Explicit push blocker: this task was instructed not to push;
remote acceptance requires lead integration under ZANA remote policy
(fetch/reconcile, non-force push, confirmed remote SHA). No remote SHA is
claimed.

## Canonical integration receipt

- accepted canonical commit: `ea317050505358702fa039bc6a2c20da2dc605b5`
- canonical branch: `master`, tracking `origin/main`
- changed product paths: `core/zana_core/observability/**`,
  `core/tests/observability/**`, `core/zana_core/streaming/redaction.py`, and
  `core/tests/streaming/test_redaction.py`
- post-integration gates: 226 focused Observability/Streaming tests passed;
  Ruff check and format passed; Pyright reported 0 errors and 0 warnings;
  cached diff hygiene passed
- security delta: telemetry remains disabled; event payloads, display fields,
  sink results, retention, serialization, local JSONL writes, and rotation are
  bounded and fail closed; C0/DEL-bearing or corrupted operation IDs are never
  reflected to callers
- residual risk: synchronous fsync cost and best-effort rotation rollback are
  explicit; live application logging is deferred to the later API/runtime
  wiring milestone
- push proof: non-force `master:main` push succeeded and `origin/main` was
  confirmed at `ea317050505358702fa039bc6a2c20da2dc605b5`
- clean proof before receipt staging: index and worktree diffs both exited 0
