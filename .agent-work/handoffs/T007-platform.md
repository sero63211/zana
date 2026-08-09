# T007-platform Handoff - Canonical Cross-Platform Path and Storage Boundary

Verdict: PASS

## Scope

Implemented the tiny canonical cross-platform path/storage boundary for the
lightweight-architecture wave, starting exactly from integrated commit
`045b0e9`. Only owned paths were touched:

- `core/zana_core/platform/**`
- `core/tests/platform/**`
- `.agent-work/handoffs/T007-platform.md`

No filesystem mutation happens on module import or simple resolution. No
model/runtime/inference/training/embedding/download, package install, venv
sync, native build, background thread, daemon, timer, telemetry, polling, or
recursive scan exists anywhere in this lane. Production code contains no
hard-coded `/Users`, HOME, drive, slash, or host-specific path.

## Changed files and modules

- `core/zana_core/platform/models.py` - frozen strict models with
  `extra="forbid"`: `PathRoot` enum (config/data/cache/log/temp/workspace),
  `PathPolicy` (component/path-length/depth budgets, home/cwd/filesystem-root
  guards, disjoint-root and confinement rules), `PlatformPaths` with
  `root(kind)`/`all_roots()`, and `FilesystemCapability` with honest unknown
  `writable`/`free_bytes`/`error` fields. `PlatformPathError` and
  `PlatformPathValidationError` carry stable machine-readable codes.
- `core/zana_core/platform/resolve.py` - injection-friendly `PathLocator`
  protocol; `PlatformdirsLocator` backed only by `platformdirs` (config/data/
  cache/log) plus stdlib `tempfile.gettempdir()` for temp (platformdirs 4.11
  exposes no temp function) and a versioned workspace under data; and
  `FixedPathLocator` for tests. `PathResolver.resolve()` performs zero
  filesystem mutation. `validate_override`/`validate_overrides` reject
  relative, NUL, traversal (`.`/`..`), filesystem/home/cwd roots, alias
  collisions, parent/child configurations, unconfined paths, and budget
  overflows. `derive_child` validates components, depth/length budgets, pure
  lexical containment, and symlink-escape via resolved comparison when the
  root exists; it never creates directories. `is_within` is a pure lexical
  helper.
- `core/zana_core/platform/probe.py` - `FilesystemProbe` protocol and
  `DefaultFilesystemProbe` with a constant number of operations per root
  (`is_dir`, `os.access`, `shutil.disk_usage`). Failures produce honest
  unknown fields plus an error string, never fake zero/success. `probe_roots`
  probes exactly the six approved roots.
- `core/zana_core/platform/ensure.py` - explicit, shallow, idempotent
  `ensure_roots` that creates only the exact approved roots (one `mkdir`
  per root) and never recursively scans; failures raise `PlatformPathError`.
- `core/tests/platform/**` - 34 focused tests covering the three OS layouts
  (macOS/Linux/Windows) via injected locators, explicit overrides, unsafe
  broad roots, traversal, NUL, alias/parent-child collisions, confinement,
  idempotent ensure, permission/stat failures, unknown free space, zero
  creation during resolution, symlink escape, depth/length budgets, strict
  model frozenness, and injected probe usage. No `pytest.MonkeyPatch` is
  used; all OS/probe simulation is constructor injection.

## Checks run and evidence

All commands used the existing shared environment at
`/Users/sero/Documents/zana/core/.venv/bin`; no dependencies were installed
and no venv was created.

| Check | Command | Result |
| --- | --- | --- |
| Focused platform tests | `core/.venv/bin/python -m pytest core/tests/platform -q` | 34 passed |
| Full Core suite | `core/.venv/bin/python -m pytest core/tests -q` (escalated for loopback/network) | 734 passed |
| Ruff lint | `core/.venv/bin/ruff check core/zana_core/platform core/tests/platform` | clean |
| Ruff format | `core/.venv/bin/ruff format --check core/zana_core/platform core/tests/platform` | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core/platform` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

## Security delta

- No filesystem writes on import/resolution; explicit overrides must be
  absolute, normalized, NUL-free, non-traversing, non-root/home/cwd, and
  pairwise disjoint or confined before any root can be used.
- Symlink escapes are rejected during child derivation; traversal components,
  separators, NUL bytes, depth, and length budgets fail closed.
- Capability probes are bounded and mutation-free; unknown availability/
  writability/free space is represented honestly rather than fabricated.
- Directory creation is restricted to the exact approved roots, idempotent
  and shallow, so no broad cleanup target can be introduced accidentally.

## Residual risk

- `PlatformdirsLocator` TEMP uses stdlib `tempfile.gettempdir()` because
  platformdirs 4.11 exposes no temp helper; this is OS-derived, not
  hard-coded, and is documented in the module docstring.
- The platform boundary is not wired to API/DB; the integration lane owns
  using these roots for builds, export/import, and diagnostics.
- `os.access` reflects the real effective user's permissions; on elevated
  accounts writability may report differently than actual write behavior,
  which the honest `writable` field is designed to surface.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `2dc004f` (`feat: add canonical platform path boundary`)
  on branch `agent/T007-platform`, started exactly at integrated commit
  `045b0e9`.
- This handoff is committed separately on the same branch.
- Cherry-pick both commits onto the integration lane. No lockfiles, DB
  schema, API registration, desktop files, or GoalBuddy state are included.
  After integration, rerun the focused platform suite and the full Core
  suite with loopback permission.
