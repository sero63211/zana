# T920 Rust Core Foundation Handoff

Verdict: PASS

## Scope

Implemented the dependency-complete Rust M0 foundation on the isolated
worktree `/Users/sero/.codex/worktrees/af77/zana`, branch
`agent/t920-rust-core-foundation`, starting exactly from clean detached
`ce806b5bddbc82a5361092d199ed2a4553bf6b66` (remote `origin`, HTTPS
`github.com/sero63211/zana.git`).

Only exclusive owned paths were written:

- new root `Cargo.toml` and `Cargo.lock`
- new `crates/zana-core/**`
- new `crates/zana-core-server/**`
- `scripts/package-core.sh`
- `apps/desktop/src-tauri/Cargo.toml`
- `.agent-work/handoffs/T920-rust-core-foundation.md`

`apps/desktop/src-tauri/src/supervisor.rs`, `lib.rs`, `commands.rs`,
`tauri.conf.json`, and the desktop `Cargo.lock` were inspected and kept
unchanged because the Rust binary preserves the stable sidecar identity,
CLI/env token contract, and invoke contract exactly. No Python, TypeScript,
Android, GoalBuddy, ledger, migration, or unrelated file was written.

## Changed modules and behavior

- Root workspace (`Cargo.toml`, `Cargo.lock`): lean two-crate Rust workspace
  with `rusqlite` (bundled), `serde`, `serde_json`, and `signal-hook`.
- `crates/zana-core`: shared typed primitives for errors, bearer auth,
  platform data-root resolution, and SQLite bootstrap.
- `crates/zana-core-server`: runnable `zana-core` binary; loopback-only
  bounded HTTP server, exact `GET /api/v1/health`, sanitized canonical JSON
  error envelope, CORS, graceful signal shutdown, and deterministic child
  cleanup.
- `scripts/package-core.sh`: restored exactly to the accepted base
  Python/PyInstaller transitional packaging. T920 introduces no Rust
  packaging cutover; the canonical sidecar remains Python-only rollback
  evidence until T925 performs the real Python-free cutover after T921/T922
  route parity.
- `apps/desktop/src-tauri/Cargo.toml`: minimal empty `[workspace]` table so
  the desktop package is isolated from the new root workspace. No dependency
  or lockfile change was required.

## Health contract

`GET /api/v1/health` requires `Authorization: Bearer <per-launch token>`,
returns 200 with `{status: "ok", version, python_version, pid,
uptime_seconds}`. `python_version` is the string `"not-required"` so the
accepted desktop client validation remains satisfied while honestly
reporting that Python is no longer required. Missing/wrong/malformed bearer
tokens return 401 with the canonical `error.code == "UNAUTHORIZED"` envelope.
Loopback CORS matches the accepted origin set and methods.

## Checks run and evidence

Host-safety gates only; no release package/native bundle, app/browser/device,
Python suite, model/provider/download, inference, training, load, or broad
test ran. The lead explicitly directed not to run the release package/native
bundle gate today.

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo check --workspace` | PASS |
| `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| `cargo test --workspace` | PASS, 21 lib + 19 server tests |
| `bash -n scripts/package-core.sh` | PASS |
| `jq empty apps/desktop/src-tauri/tauri.conf.json` | PASS |
| `cargo metadata` from root and `apps/desktop/src-tauri` | PASS |
| `git diff --check` before and after commit | PASS |
| `scripts/package-core.sh` mode | `100755` (`-rwxr-xr-x`) |

## Security delta

- Loopback-only bind (`127.0.0.1` or `::1`), never a non-loopback address.
- Bearer token is header-only and never appears in URLs, logs, or error
  bodies; tokens over 512 bytes fail closed before comparison.
- Bounded HTTP parsing: 16 KiB headers, 64 headers, 1 MiB body, 5 s
  connection timeout, 8 concurrent connections with a busy 503 response;
  oversized and malformed requests map to bounded canonical errors.
- Buffered body bytes after the header terminator are consumed correctly and
  undisclosed pipelined bytes are rejected.
- Platform data root is validated before mutation; symlinked roots and
  database files fail closed; parent symlink escapes are rejected after
  canonicalization. `NotFound` is represented as `Option<Metadata>`, with no
  unsafe or synthesized metadata.
- SQLite opens with WAL, foreign keys, and 30 s busy timeout, refuses
  symlinked/corrupt files, and does not fake migration state: the existing
  `alembic_version` table is never claimed by the Rust bootstrap.
- Accepted streams are explicitly restored to blocking mode with bounded 5 s
  read/write timeouts before workers or busy responses use them; a stream
  that cannot be made blocking is dropped/closed safely.
- Windows data-root resolution matches the accepted Python on-disk contract
  (`%LOCALAPPDATA%\zana\zana`) instead of creating a second sibling database.
- SQLite metadata handling fails closed: only `NotFound` may proceed, symlinks
  are rejected, and every other metadata error rejects before parent creation
  or database open.
- Canonical errors are sanitized and fixed; raw paths, OS errors, tokens, and
  environment details are never exposed.

## Residual risk

- The Rust server currently exposes only the health surface, as scoped;
  product routes, migrations, and parity with the Python Core remain later
  T920 lanes.
- The canonical desktop sidecar is intentionally still built by the accepted
  Python/PyInstaller `scripts/package-core.sh` as transitional rollback
  evidence. The final MVP Python-free packaging cutover is mandatory but not
  part of this milestone.
- The std-only HTTP parser handles HTTP/1.1 requests with `Content-Length`
  or identity transfer encoding only and rejects other encodings. No live
  server/browser/native bundle or release package ran under host safety.
- The sidecar port reservation race documented by T900 remains unchanged:
  the shell plugin cannot pass a pre-bound socket.
- The delayed-client socket integration test binds real loopback sockets and
  therefore ran with the focused test approval outside the filesystem
  sandbox; no live app, browser, native bundle, or release package ran.

## Blockers

None.

## Merge instructions

Merge the accepted commit stack (`8e8b119` product, `fb2306c` correction,
`5708d06` second correction, `57ee166` third correction, `e69dbe2` fourth
correction, plus this receipt) onto the canonical lane at base
`ce806b5bddbc82a5361092d199ed2a4553bf6b66`.
The desktop supervisor and `tauri.conf.json` need no merge-side change;
`scripts/package-core.sh` must remain the accepted Python/PyInstaller version.
The Rust `zana-core` binary remains an available runnable foundation, not the
shipped product switch.
Canonical main commit `8c0387b1adaf27f6522beaf6223ae891e41b48a8` (pushed,
`origin/main` exact same SHA) formally reconciles T920 as a standalone Rust M0
and keeps the functional Python desktop package/supervisor until T921+T922
route parity; T925 owns the atomic package cutover and Python removal.
After integration, rerun the focused Rust workspace gates; the release
package/native bundle gate remains deferred by host safety.

## Correction addendum

Independent lead review found three defects in product commit `8e8b119`, all
repaired in the same exclusive scope by correction commit `fb2306c`:

1. Accepted `TcpStream` could inherit nonblocking mode from the nonblocking
   listener. `prepare_accepted_stream` now calls `set_nonblocking(false)`
   before bounded read/write timeouts, and a failed transition drops/closes
   the stream before any handler or busy response sees it. A deterministic
   delayed-client integration test connects first, waits 150 ms, sends the
   authenticated health request, and receives `HTTP/1.1 200 OK`.
2. Windows data-root derivation now matches
   `platformdirs.user_data_dir("zana")` exactly:
   `%LOCALAPPDATA%\zana\zana`. The derivation was refactored into a pure
   `data_root_for_platform(PlatformKind, ...)` helper with an exact Windows
   parity test runnable on this macOS host; macOS and Linux paths are
   unchanged.
3. `Database::open` now treats `symlink_metadata` failures fail-closed: only
   `NotFound` proceeds, a symlink rejects, and every other metadata error
   rejects before parent creation or database open. Focused tests inspect
   every helper branch and prove a path under a regular file creates nothing.

### Correction gates

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo check --workspace` | PASS |
| `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| `cargo test --workspace` (escalated for loopback sockets) | PASS, 18 lib + 19 server tests |
| `bash -n scripts/package-core.sh` | PASS |
| `jq empty apps/desktop/src-tauri/tauri.conf.json` | PASS |
| `cargo metadata` from root and `apps/desktop/src-tauri` | PASS |
| `git diff --check` before and after correction commit | PASS |

### Correction security delta

Streams are blocking with bounded timeouts before I/O, Windows parity avoids
silent dual-database migration, and metadata errors fail closed before
filesystem mutation. The original security delta remains unchanged.

### Correction residual risk

The socket integration test is local and deterministic; live Tauri/native
bundle and release-package behavior remain deferred. The sidecar port
reservation race remains as documented.

## Second correction addendum

Independent Codex review found the T920 tree must not switch the canonical
sidecar packaging to the health-only Rust server while the desktop still
consumes product routes. Second correction commit `5708d06` repairs both
findings in the same exclusive scope:

1. `scripts/package-core.sh` was restored byte-for-byte to the accepted base
   behavior (`git diff ce806b5 -- scripts/package-core.sh` is empty). No
   dual-mode flag, second versioned script, placeholder route, or conditional
   abstraction was added, and no Cargo target-directory assumption remains.
   T925 owns the one real Python-free packaging cutover after T921/T922 route
   parity.
2. Linux `XDG_DATA_HOME` empty or whitespace-only values now fall back to
   `HOME/.local/share` exactly like the accepted `platformdirs` contract.
   Exact pure unit tests cover empty, whitespace, and non-empty absolute
   XDG values; macOS/Windows derivations and fail-closed root validation are
   unchanged.

### Second correction gates

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo check --workspace` | PASS |
| `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| `cargo test --workspace` (escalated for loopback sockets) | PASS, 21 lib + 19 server tests |
| `bash -n scripts/package-core.sh` | PASS |
| `jq empty apps/desktop/src-tauri/tauri.conf.json` | PASS |
| `cargo metadata` from root and `apps/desktop/src-tauri` | PASS |
| `git diff --check` before and after correction commit | PASS |
| `git diff ce806b5 -- scripts/package-core.sh` | empty (exact restore) |

### Second correction security delta

No new attack surface: packaging reverts to the accepted transitional
Python/PyInstaller path, and XDG whitespace handling avoids an invalid
data-root base while preserving fail-closed root validation.

### Second correction residual risk

The Rust foundation is available but the shipped sidecar remains Python-only
until T925; desktop product routes remain Python-backed until T921/T922
parity.

## Third correction addendum

Independent Codex rerun reported two accepted findings and one stale-board
finding rejected by the lead. Third correction commit `57ee166` repairs the
two accepted findings in the same exclusive scope:

1. Total request deadline: the 5 s socket timeout previously reset after each
   successful read, so a slow-drip peer could occupy an 8-slot worker
   indefinitely and shutdown joins could block. `read_request_with_clock`
   now enforces one monotonic wall-clock deadline across headers plus body and
   rechecks it before every read, including after `Interrupted`. On expiry it
   returns `ParseError::Timeout`, the handler answers 408, and the slot is
   released. Deterministic fake-clock tests prove repeated successful partial
   reads, slow body drains, and `Interrupted` retries cannot extend the total
   budget; no sleeping test or framework was added.
2. Startup token validation: `zana_core::auth::valid_launch_token` is now a
   shared predicate used by both startup and request verification, so the two
   paths cannot drift. Before any filesystem/database mutation or server
   launch, `resolve_launch_token` rejects empty, whitespace-containing, and
   over-`MAX_TOKEN_BYTES` CLI/env tokens with a fixed sanitized error that
   prints neither token content nor length. Focused auth and startup-boundary
   tests cover both sources without launching the server.

The third board finding about canonical `main` would have required touching
PM-owned GoalBuddy state that this old-base isolated branch intentionally
does not contain; the lead rejected it because canonical main commit
`a83a296` already registers/activates T920. No `state.yaml`, ledger, or other
PM-owned file was touched, and this handoff remains the only coordination
write in this worktree.

### Third correction gates

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo check --workspace` | PASS |
| `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| `cargo test --workspace` (escalated for loopback sockets) | PASS, 23 lib + 23 server tests |
| `bash -n scripts/package-core.sh` | PASS |
| `jq empty apps/desktop/src-tauri/tauri.conf.json` | PASS |
| `cargo metadata` from root and `apps/desktop/src-tauri` | PASS |
| `git diff --check` before and after third correction commit | PASS |

### Third correction security delta

Slow-drip peers are bounded by one total request deadline instead of
per-read idle time, so worker slots and shutdown are bounded; `Interrupted`
cannot reset the clock. Launch tokens are validated before any filesystem
mutation using the same predicate as authentication, and startup errors stay
fixed/sanitized with no secret content or length disclosure.

### Third correction residual risk

The request deadline is enforced at parse boundaries with a monotonic clock;
live Tauri/native bundle and release-package behavior remain deferred. The
sidecar port reservation race and Python-only transitional packaging remain
as documented.

## Fourth correction addendum

Independent review accepted P2: the parser checked the monotonic deadline
before each read, but a real `TcpStream` retained a fixed 5 s read timeout, so
a peer sending a byte just before the deadline could make the next read block
almost another 5 s. Fourth correction commit `e69dbe2` fixes this at the
production socket boundary:

1. A small `ReadWithTimeout` boundary is used for every parse read. The
   production `TcpStream` implementation calls `set_read_timeout(Some(remaining))`
   before each blocking read, where `remaining` is the one total monotonic
   deadline minus the current clock.
2. Expiry is checked before and after every successful read, so a single read
   that crosses the total budget returns `ParseError::Timeout`/408 and releases
   the worker slot. `Interrupted` still cannot extend the budget, and buffers,
   caps, the 8-connection limit, and shutdown behavior are unchanged.
3. Deterministic fake-clock tests prove (a) one read crossing the budget
   fails after the read and (b) the remaining per-read timeout strictly
   decreases across reads, without multi-second sleeps or a new framework.

### P1 canonical reconciliation

Independent P1 (premature health-only Rust cutover over real Python routes)
was resolved by canonical board reconciliation, not by pretending the cutover
occurred. Canonical main commit/push
`8c0387b1adaf27f6522beaf6223ae891e41b48a8` (exact same SHA on `origin/main`,
clean) formally changes T920 to a standalone Rust M0 and preserves the
functional Python desktop package/supervisor until T921+T922 route parity;
T925 owns atomic package cutover/Python removal. The founder directive now
explicitly forbids wiring the foundation-only Rust server over the functional
Core. This worktree intentionally does not contain PM-owned board state and
did not modify it; the earlier stale `a83a296` note is superseded by the
canonical `8c0387b` reconciliation.

### Fourth correction gates

| Check | Result |
| --- | --- |
| `cargo fmt --all --check` | PASS |
| `cargo check --workspace` | PASS |
| `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| `cargo test --workspace` (escalated for loopback sockets) | PASS, 23 lib + 25 server tests |
| `bash -n scripts/package-core.sh` | PASS |
| `jq empty apps/desktop/src-tauri/tauri.conf.json` | PASS |
| `cargo metadata` from root and `apps/desktop/src-tauri` | PASS |
| `git diff --check` before and after fourth correction commit | PASS |

### Fourth correction security delta

Every blocking socket read is now bounded by the remaining total request
deadline rather than a fixed per-read idle timeout, so slow-drip peers cannot
occupy worker slots beyond the total budget; post-read deadline checks prevent
a single crossing read from succeeding.

### Fourth correction residual risk

Live Tauri/native bundle and release-package behavior remain deferred; the
canonical sidecar remains Python-only until T921/T922 parity and T925 cutover.
The sidecar port reservation race remains as documented.

### Final review status

Pending lead rerun after the fourth-correction code commit and this truthful
receipt.

## Accepted commit stack and clean proof

- Product commit: `8e8b119aea63caeade6f6e56f9bab08394901d32`
- Correction commit: `fb2306c16a97d84b9bf6df6b3985d88ab4afe87e`
- Second correction commit: `5708d06256f7056c8b49d0919826074fd746d131`
- Third correction commit: `57ee166cd854e5fad6b7cd388f71f2ccf55cba7c`
- Fourth correction commit: `e69dbe23d0efefe9276fd4af714f26ebec92378c`
- Receipt commit: this handoff commit (resolve with `git rev-parse HEAD`)
- Branch: `agent/t920-rust-core-foundation`
- Remote: `origin https://github.com/sero63211/zana.git`; no push attempted
  (explicit T920 no-push policy).
- Clean proof after fourth correction commit: `git status --porcelain` empty
  and `git diff --check` clean; the handoff commit adds only this file.
