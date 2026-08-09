# T900 Tauri Core Packaging and Lifecycle Handoff

Verdict: PASS

## Scope

Implemented the Tauri Core sidecar lifecycle and packaging vertical on the
isolated worktree `/Users/sero/.codex/worktrees/edc1/zana`, branch
`agent/t900-tauri-core-package`, starting exactly from base
`c99546653002166488dfd4e222ad2637e71d7f36`.

Only exclusive owned paths were written:

- `apps/desktop/src-tauri/src/lib.rs`
- `apps/desktop/src-tauri/src/commands.rs`
- `apps/desktop/src-tauri/src/errors.rs`
- `apps/desktop/src-tauri/src/loopback.rs`
- `apps/desktop/src-tauri/src/secret.rs`
- `apps/desktop/src-tauri/src/supervisor.rs`
- `scripts/package-core.sh`
- `scripts/dev.sh`
- `.agent-work/handoffs/T900-tauri-core-package.md`

`tauri.conf.json`, `capabilities/default.json`, `build.rs`, `Cargo.toml`, and
`Cargo.lock` were inspected but intentionally not changed: the existing CSP is
already loopback-only, the capability set is already minimal (`core:default`),
and no dependency additions were required.

## Changed modules and behavior

- `supervisor.rs` (new): one `CoreSupervisor` owns exactly one Core child.
  Every launch reserves a fresh loopback port and a fresh OS-CSPRNG token,
  spawns `zana-core serve --host 127.0.0.1 --port <port>` with the token
  supplied only via `ZANA_CORE_TOKEN`, and stores the current connection for
  the frozen `core_connection` invoke contract. A lifecycle mutex serializes
  startup, restart, and shutdown so two children cannot be spawned. Restart
  first stops the prior child; a cleanup or spawn failure is sanitized and
  surfaces through `launchError` without spawning a replacement. App exit
  kills the child with an expected-stop flag so the watcher does not report a
  false failure.
- `watch_core_events` (in `supervisor.rs`): one bounded Tauri async task per
  child drains the plugin's spawn event receiver until the channel closes after
  `Terminated`, so no polling, busy loop, or unbounded event retention exists.
  Unexpected `Terminated`/`Error` events clear the dead connection and set an
  honest, sanitized `launchError`; generation tags prevent a stale watcher from
  touching a newer child.
- `errors.rs` (new): fixed sanitized messages. Raw plugin/OS errors, paths,
  commands, environment values, and tokens are never forwarded.
- `loopback.rs` (new): reserves a port by binding only `127.0.0.1:0`; no port
  scanning, no non-loopback address. The bind/release window is the smallest
  the shell plugin's sidecar API permits because no pre-bound socket can be
  passed to the child.
- `secret.rs` (new): 256-bit token from `/dev/urandom` on Unix, with a uuid
  v4/getrandom fallback on non-Unix. Token appears only in the child
  environment and in the invoke response.
- `commands.rs` (new): `core_connection` and `restart_core` commands with the
  unchanged invoke names.
- `lib.rs`: reduced to module declarations, builder/setup wiring, and exit
  cleanup. A Core launch failure no longer blocks the desktop shell; it is
  reported through `launchError`.
- `scripts/package-core.sh`: strict repo/target/path validation, host-triple
  enforcement (no cross-arch mislabel), toolchain presence checks that fail
  honestly before work, repo-local temp staging with EXIT cleanup, PyInstaller
  output verification, executable `install -m 755`, and atomic same-directory
  `mv` publication under `apps/desktop/src-tauri/binaries/`.
- `scripts/dev.sh`: loopback-only host validation, bounded numeric port
  validation (1-65535, no leading-zero ambiguity), fresh `secrets.token_hex(32)`
  token per run unless `ZANA_CORE_TOKEN` is explicitly supplied, token passed
  only via environment, never printed, and never passed as a CLI argument.

## Checks run and evidence

The host-safety override was respected: no cargo build/check/clippy/test, Tauri
build/bundle/dev, PyInstaller, app/browser/device, live Core/sidecar, model,
network, container, or load test ran, and no dependency or lockfile changed.

| Check | Result |
| --- | --- |
| `bash -n scripts/package-core.sh scripts/dev.sh` | PASS |
| `cargo fmt --check` (owned Rust modules) | PASS |
| `git diff --check` before and after commit | PASS |
| JSON sanity parse of `tauri.conf.json` and `capabilities/default.json` | PASS |
| dev.sh invalid host `0.0.0.0` | exit 1, loopback-only sanitized error |
| dev.sh invalid port `99999` | exit 1, bounded sanitized error |
| target-triple regex tests (valid/space/path/leading-dash) | PASS |
| port validation tests (0, 1, 65535, 65536, empty, alpha, leading-zero) | PASS |
| grep assertions: `core_connection`, `restart_core`, camelCase `baseUrl`/`token`/`launchError` preserved | PASS |
| grep assertions: no polling loop, no `--token` argument, token only via env | PASS |
| Tauri shell plugin source inspection (`tauri-plugin-shell` 2.3.5) | `CommandEvent::Terminated`, `Receiver`, `CommandChild::kill` signatures confirmed; no `on_event` API exists in this version |

## Security delta

- Per-spawn 256-bit CSPRNG bearer token; never on CLI, logs, disk, generated
  config, or error text.
- Loopback-only port reservation and sidecar host; CSP remains loopback-only;
  capability permissions remain minimal with no frontend shell surface.
- Restart cannot leave two children; cleanup/spawn/kill failures are sanitized
  and actionable with no path, command, env, token, or raw provider detail.
- Unexpected Core exits produce an honest `launchError`; dead connections are
  cleared instead of being presented as healthy.
- Poisoned locks degrade to fixed sanitized messages or recovered state.
- Packaging script enforces ZANA-path containment, host target triple, atomic
  publication, and no model downloads or secret output.

## Residual risk

- The sidecar API cannot accept a pre-bound socket, so the OS-chosen port is
  released immediately before spawn; another local process could theoretically
  claim it in the small window. This is the minimum race the available API
  allows and no scanning is performed.
- A kill failure after a child already exited can surface once as a restart
  cleanup error; a retry then proceeds because the exited child slot is gone.
- Cargo compilation, clippy, Rust unit tests, Tauri build/bundle/dev launch,
  PyInstaller packaging, and live Core health were intentionally deferred under
  the host-safety override.

## Blockers

None.

## Merge instructions

Merge the single implementation commit `78d09d5` and then this handoff receipt
commit onto the canonical lane at base `c995466`. No lockfile, generated schema,
icon, frontend, Core Python, root manifest, GoalBuddy, or ledger file is
included. After integration, rerun `cargo fmt --check`, `bash -n` on both owned
scripts, and the previously deferred compile/package/live gates when the host
safety policy is lifted.

## Accepted commit and clean proof

- Implementation commit: `78d09d50bb8a3a68c50352c915105cbb7c286dae`
- Receipt commit: this handoff commit (resolve with `git rev-parse HEAD`).
- Branch: `agent/t900-tauri-core-package`
- Remote: none; no push attempted (explicit push blocker is lead integration).
- Clean proof after implementation commit: `git status --porcelain` empty and
  `git diff --check` clean; the handoff commit adds only this file.

## Lead correction addendum

Focused lead lifecycle/security review found and repaired direct defects in the
same exclusive scope. No compile/build/test/live execution was performed.

### Repairs

1. Stale watcher race: unexpected exits now go through one
   lifecycle-serialized, generation-checked transition (`on_unexpected_exit`).
   The watcher never clears the slot and then writes the connection in two
   separate steps; a stale generation is a no-op for both.
2. `CommandEvent::Error`: the watcher records a sanitized failure for the same
   generation through `on_event_error` and keeps draining the bounded channel.
   On channel closure, `on_channel_closed` performs best-effort cleanup for
   exactly that generation. A kill that cannot be confirmed sets
   `replacement_blocked`, and `launch` then refuses to spawn a replacement.
3. Post-spawn state publication: `lock()` recovers poisoned mutexes, and there
   are no fallible operations after a successful sidecar spawn, so a spawned
   child is never orphaned by an early return. An invariant comment documents
   that any future early return must kill the child first.
4. Non-Unix token fallback now concatenates three independent OS-backed
   UUIDv4 draws (366 random bits, at least 256). The Unix 32-byte
   `/dev/urandom` path is unchanged.
5. Real uv execution now uses `--no-sync` in both `package-core.sh`
   (toolchain probe and PyInstaller) and `dev.sh` (Core serve), preserving the
   offline/lightweight contract.
6. `package-core.sh` no longer prints the PyInstaller log tail or absolute
   root/destination paths; failures and success use fixed, actionable,
   repo-relative messages only.
7. Publication containment: canonical path resolution rejects symlinked Core
   entry and sidecar directory paths that escape the repository; publication
   uses an exclusive same-directory `mktemp` file (no predictable `.tmp.$$`);
   cleanup traps cover EXIT/INT/TERM/HUP and remove only the exact temp file
   and staging directory; atomic `mv` and executable mode are preserved.

### Correction checks

| Check | Result |
| --- | --- |
| `cargo fmt --check` (owned Rust modules) | PASS |
| `bash -n scripts/package-core.sh scripts/dev.sh` | PASS |
| `git diff --check` | PASS |
| JSON sanity parse of touched config | PASS |
| dev.sh invalid host/port/leading-zero cases | exit 1 with sanitized errors |
| package-core symlinked Core entry | exit 1, sanitized symlink rejection |
| package-core symlinked binaries directory escaping repo | exit 1, sanitized containment rejection |
| grep invariants: generation-checked transitions, no stale `forget_exited_child`, `replacement_blocked`, `--no-sync` on real executions, no raw log tail/absolute path prints | PASS |

### Residual risk after correction

- The sidecar API still cannot pass a pre-bound socket, so the OS-chosen port
  is released immediately before spawn. The existing API provides no way to
  remove that small window; no port scanning is performed.
- If `CommandChild::kill` returns an error, cleanup is conservatively treated
  as uncertain: the connection stays failed and no replacement is spawned
  until the app is restarted.
- Cargo compilation, clippy, Rust unit tests, Tauri build/bundle/dev launch,
  PyInstaller packaging, and live Core health remain intentionally deferred
  under the host-safety override.

### Correction commits

- Correction commit: `4a611b7e977b42d44f5db81ed8a19a6cfa70abb7`
- Handoff update commit: this handoff commit (resolve with `git rev-parse HEAD`).
- The handoff update commit also restores the owned packaging script's
  executable mode (`100755`).
