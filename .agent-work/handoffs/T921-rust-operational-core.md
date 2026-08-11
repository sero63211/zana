# T921 Rust Operational Core Handoff

Verdict: PASS (scope complete; compatibility API routes now T921B-exclusive)

## Scope

Implemented the dependency-complete Rust operational control plane inside
`crates/zana-core/**` on the isolated worktree
`/Users/sero/.codex/worktrees/1668/zana`, branch
`agent/t921-rust-operational-core`, starting from clean detached
`8c11ac4f53993e826323418d69e7f9e4675fcd22` (remote `origin`,
`https://github.com/sero63211/zana.git`).

Only exclusive owned paths were written:

- root `Cargo.toml` and `Cargo.lock`
- `crates/zana-core/**`
- exact focused Rust tests colocated in those paths
- `.agent-work/handoffs/T921-rust-operational-core.md`

No Python, TypeScript, Tauri/package/supervisor, Android, GoalBuddy
state/ledger, capability/build/evaluation/Image/Instance/knowledge/portability
product logic, generated binaries/caches, or unrelated file was written.

## Ownership split (lead directive)

`crates/zana-core-server/**` is exclusively reassigned to the separate visible
T921B Flash-Max API task. T921 did not read-write, modify, stage, or plan
edits in that directory after the split. The authenticated compatibility API
routes are therefore not part of this T921 handoff and remain pending T921B.

## Changed modules and behavior

### SQLite, repositories, settings

- Idempotent operational schema (`runtimes`, `models`, `jobs`, `job_events`,
  `settings`, `resource_snapshots`, `audit_events`) compatible with the
  accepted Python tables; existing Python tables are reused untouched and no
  `alembic_version` state is ever claimed.
- Repositories for runtimes, models, jobs, events (SQL-side bounded SSE
  projection), settings, audit events, and resource snapshots.
- Bounded settings service with sensitive-value redaction.
- Jobs service with legal transitions, atomic status-predicate writes,
  automatic event retention, restart recovery, and service-level text bounds.

### Jobs and events

- Persistent generic job lifecycle with legal transitions, cancellation,
  monotonic event IDs, bounded retention, and fetch/SSE-compatible replay
  projection semantics (SQL truncation plus error sentinel).
- Unknown/corrupt DB enum decoding fails closed.

### Resources and System Doctor

- Bounded resource governor with explicit budgets, admission, checked
  arithmetic, stale-snapshot fail-closed heavy admission, and bounded usage
  history.
- System Doctor with one process-wide fixed bounded probe executor,
  per-check/run deadlines, cumulative output budgets, truthful
  unsupported/limited states, and semantic health classification before
  output trimming.

### Runtime discovery and loopback transport

- Bounded local runtime/model discovery for Ollama, LM Studio, llama.cpp,
  MLX-LM, and explicit local OpenAI-compatible registrations using injected
  transports, process/filesystem boundaries, exact identity/digest/size
  metadata, and no remote fallback.
- Private validated registry config revalidated per probe; canonical
  runtime-id/collision checks; byte-exact output bounds; no partial
  Online-success on model overflow; strict loopback-only transport with one
  total deadline, raw header/status/UTF-8 hardening, bracketed IPv6 Host, and
  documented cooperative timeout contract.

### Acquisition

- Approved model acquisition planning/execution boundary with injected
  transport/line source and filesystem/disk admission, explicit user approval,
  disk preflight, no automatic/silent download, bounded progress/events/output,
  cancellation, guaranteed close on every path, restart/shutdown truth, and
  strict persisted-request/identity validation.

### Observability, audit, and SSE

- Bounded local observability events with strict redaction, canonical compact
  JSON serialization, bounded retention registry/pages/health, and SQLite
  audit persistence with page/trim helpers.
- Bounded canonical SSE encoder, cursors, terminal semantics, and total/event
  byte caps for T921B route consumption.

## Checks run and evidence

Host-safety gates only; no live runtime/model/provider/network, download,
inference, training, app/browser/device, bundle, broad/load test or
dependency install ran.

| Check | Result |
| --- | --- |
| `cargo fmt --all -- --check` | PASS |
| `cargo check --workspace` | PASS |
| `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| `cargo test -p zana-core` | PASS, 103 lib tests |
| `cargo test -p zana-core acquisition::tests` | PASS, 14 focused tests |
| `cargo test -p zana-core runtimes::tests` | PASS, 29 focused tests |
| `cargo test -p zana-core --test operational_db` | PASS, 13 integration tests |
| `git diff --check` | PASS |

## Accepted commit stack (local, no push)

- `b2cb214` feat: add Rust operational DB, jobs, and settings plane
- `887698a` feat: add Rust resource governor and system doctor
- `b246ce4` fix: checked macOS page math and Mach port release
- `885601d` fix: harden DB jobs and resource governor invariants
- `7ab2807` fix: bound doctor executor, output caps, and feature readiness
- `b279b09` fix: bound doctor JSON, output budget, and feature truncation
- `47d7e3a` fix: exact cumulative doctor output budget and truncation
- `211744f` fix: classify doctor health before output bounding
- `ab8923c` fix: exact doctor semantic failure count and valid-budget tests
- `27ef8e0` feat: add bounded Rust runtime discovery and loopback transport
- `e63da0d` fix: harden runtime discovery identities, paths, and model bounds
- `10fb97a` fix: enforce runtime config, origin, byte, and transport invariants
- `a3785f2` fix: deadline show skip, origin DNS, exact bounds, and header parsing
- `2edbd8a` test: prove deadline show skip, exact body bounds, and metadata controls
- `8ce2b77` feat: add bounded model acquisition planning, execution, and supervisor
- `ddc1aeb` fix: enforce acquisition bounds, FIFO, admission, close, and payload truth
- `993e3aa` feat: add bounded observability audit and SSE primitives
- `5ac4273` fix: harden T921 FIFO proof, identifiers, retention, cursors, and SSE framing
- `7825d13` fix: structurally valid cursors, bounded SSE payloads, and observability validation

## Hardening correction addendum (lead review)

Reopened for one dependency-complete hardening correction; all six concrete
defects were fixed in `5ac4273`:

1. Acquisition FIFO proof now asserts persisted repository truth after the
   first drain (`ids[0]` SUCCEEDED, `ids[1]` PENDING) before the second drain.
2. The cumulative stream-budget test uses many individually valid short
   JSONL lines split into chunks under the per-line cap, so only the total
   event-byte budget is violated; it asserts `STREAM_OVER_BUDGET` and exactly
   one close.
3. Observability builds one consistent sanitized identifier snapshot for every
   outward path; path-like, control-bearing, syntax-invalid,
   sensitive-lookalike, and overlong identifiers are replaced with a stable
   salted digest before serialization, retention, audit, page projections, and
   returned event IDs. Adversarial tests prove raw values never leak.
4. Retention truth: a successfully serialized record that immediately
   self-evicts reports `dropped=true` with exact counters/bytes.
5. SSE cursors are bounded and injection-safe, preserve plain numeric plus
   `jobs:<n>` compatibility, expose explicit `source_matches`, and support
   `allow_ahead=false` (`Invalid` instead of dead `Ahead` semantics).
6. SSE wire events emit one canonical JSON value per complete block; error
   JSON and the terminal `[DONE]` sentinel are separate blocks consumable by
   standard fetch/EventSource parsers, with caps covering all emitted bytes.

## Second hardening correction addendum

`7825d13` closes the four remaining fail-closed defects:

1. `EventCursor` fields are private with validated `new`/accessors; invalid
   construction is structurally impossible through the public API and
   `to_header` never synthesizes a valid-looking cursor. The full non-negative
   `i64` DB-id range is accepted with `i64::MAX` round-trip and negative
   expected-sequence tests.
2. SSE caps and security apply to every emitted payload: Python-compatible
   error bounds (code 64, message 500, recovery_action 300), full C0/DEL
   control rejection, and `max_data_bytes`/`max_event_bytes` checks for
   primary/error/[DONE] before `total_bytes` changes. Hostile oversized
   error/control-id tests prove a failed encode leaves the counter unchanged
   for a subsequent valid encode.
3. Observability fails closed before serialization/registry/audit mutation on
   `schema_version != 1`, non-finite/out-of-range progress, negative duration,
   and invalid/beyond-bound timestamp/message/context structure. Adversarial
   registry and SQLite audit rejection tests prove no retained row/event and
   exact failure counters.
4. Production registry/audit paths validate `RedactionLimits`; zero or
   nonsensical limits are rejected so a structured event is never retained as
   a scalar `"***"` while reporting success. Standalone `redact_value`
   remains harmless.

Sanitization happens once per outward path from one sanitized snapshot in
`serialize_event`/registry/audit.

## Security delta

- Loopback-only transport and authenticated primitives retain T920
  auth/path/error/deadline boundaries.
- Secrets, host paths, raw exceptions, transport/admission strings, and
  oversized/control-bearing identity fields never cross outward surfaces.
- Fail-closed enum decoding, stale/unknown admission, deadline exhaustion,
  unknown headroom, malformed streams, close failure, and progress/failure
  persistence errors.
- Bounded worker pools, retention, event/line/body/header caps, checked
  arithmetic, and no background/telemetry/remote transport.

## Residual risk

- No live local runtime/model/provider/network, app/browser/device, bundle,
  broad suite, performance, or load verification ran under host safety.
- The synchronous acquisition supervisor requires explicit `drain_once`
  calls; no background worker thread exists by policy.
- Compatibility API routes and `crates/zana-core-server/**` wiring are now
  exclusively T921B and were not included here.

## Blockers

None inside T921 scope. The compatibility API route milestone is not part of
this handoff after the lead ownership split.

## Merge instructions

Merge the accepted local commit stack onto the canonical lane at base
`8c11ac4` after T921B reconciliation. Do not merge `crates/zana-core-server/**`
changes from this branch (none exist). Desktop packaging remains the accepted
transitional Python sidecar until T925.

## Clean proof

`git status --porcelain` is empty after the final handoff receipt commit.

## Remote state and push blocker

- Remote: `origin https://github.com/sero63211/zana.git`
- Push: not attempted (explicit T921 no-push policy; lead integration handles
  canonical main, currently at `05c7eec`).
- Local HEAD resolves to the handoff commit above.
