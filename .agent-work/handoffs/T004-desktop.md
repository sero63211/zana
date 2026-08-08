# T004 Desktop Handoff

Verdict: PASS for authored M0 desktop source; dependency-backed verification remains an integration responsibility.

## Goal and scope

Implemented the owned Tauri 2 and React desktop surface for the frozen per-launch Core bootstrap contract. No backend, root manifest, lockfile, GoalBuddy, or ledger file was touched.

## Changed surface

- `apps/desktop/src-tauri/**`: loopback port reservation, UUID v4 launch token, sidecar spawn/restart/exit cleanup, invoke-only connection handoff, minimum capability and CSP, app bundle configuration.
- `apps/desktop/src/**`: authenticated Core health client, TanStack Query state, actionable retry, healthy/loading/failure UI, production styling and tests.
- `apps/desktop/package.json` and local TypeScript/Vite/ESLint configuration.

## Evidence and checks

- Official Tauri 2 sources checked: `https://v2.tauri.app/develop/sidecar/` and `https://v2.tauri.app/plugin/shell/` (external binaries and Rust `ShellExt::sidecar` pattern).
- Source was authored against Tauri 2, React 19, Vite 7, TypeScript 5.9 and TanStack Query 5 contracts.
- Focused checks to run after the PM installs and locks dependencies: `pnpm --dir apps/desktop lint`, `typecheck`, `test`, `build`; `cargo fmt --check` and `cargo clippy -- -D warnings` under `src-tauri`.

## Security delta

- Core binds only to a dynamically selected loopback port.
- Token is generated per desktop launch, passed to the sidecar environment, exposed only by a Tauri invoke command, and never logged or persisted.
- Web development fails closed unless both explicit API base and token are supplied.
- CSP restricts connections to loopback; the frontend treats malformed health responses as unavailable.

## Residual risk

- Exact Tauri shell sidecar identifier and lifecycle code require cargo compilation against the resolved lockfile.
- The packaged `zana-core-<target-triple>` binary must be created before an app bundle can succeed.
- UUID v4 supplies 122 random bits; future hardening may use a longer OS-random token without changing the public bootstrap shape.

## Blockers and interface changes

None. The frozen contract was preserved.

The desktop health decoder consumes the Core lane's exact response fields:
`status`, `version`, `python_version`, `pid`, and `uptime_seconds`. The sidecar
receives its launch token through `ZANA_CORE_TOKEN`, matching the Core CLI.

## Integration instructions

Merge the complete `apps/desktop/**` tree and this handoff after the Core lane. Generate root pnpm and Rust lockfiles, format the Rust source, run all checks above, then exercise one authorized and one unauthorized live health request.
