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
- `scripts/package-core.sh`: switched from uv/PyInstaller to `cargo build
  --release --locked --target "$HOST_TRIPLE" -p zana-core-server` and atomic
  publication under `apps/desktop/src-tauri/binaries/`, preserving the stable
  `zana-core-<triple>` sidecar name and executable mode.
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
| `cargo test --workspace` | PASS, 15 lib + 18 server tests |
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
- Canonical errors are sanitized and fixed; raw paths, OS errors, tokens, and
  environment details are never exposed.

## Residual risk

- The Rust server currently exposes only the health surface, as scoped;
  product routes, migrations, and parity with the Python Core remain later
  T920 lanes.
- The std-only HTTP parser handles HTTP/1.1 requests with `Content-Length`
  or identity transfer encoding only and rejects other encodings. No live
  server/browser/native bundle or release package ran under host safety.
- The sidecar port reservation race documented by T900 remains unchanged:
  the shell plugin cannot pass a pre-bound socket.

## Blockers

None.

## Merge instructions

Merge the single product commit `8e8b119aea63caeade6f6e56f9bab08394901d32`
onto the canonical lane at base `ce806b5bddbc82a5361092d199ed2a4553bf6b66`.
The desktop supervisor and `tauri.conf.json` need no merge-side change; the
Rust binary uses the same `zana-core` sidecar name, `serve --host
127.0.0.1 --port <n>` arguments, and `ZANA_CORE_TOKEN` environment variable.
After integration, rerun the focused Rust workspace gates; the release
package/native bundle gate remains deferred by host safety.

## Accepted commit and clean proof

- Product commit: `8e8b119aea63caeade6f6e56f9bab08394901d32`
- Receipt commit: this handoff commit (resolve with `git rev-parse HEAD`)
- Branch: `agent/t920-rust-core-foundation`
- Remote: `origin https://github.com/sero63211/zana.git`; no push attempted
  (explicit T920 no-push policy).
- Clean proof after product commit: `git status --porcelain` empty and
  `git diff --check` clean; the handoff commit adds only this file.
