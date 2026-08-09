# T007-platform-wiring Handoff - Canonical Platform DB Path Boundary

Verdict: PASS

## Scope

Implemented the canonical platform wiring for the Core database path,
starting exactly from integrated commit `601432f`. Only owned paths were
touched:

- `core/zana_core/main.py`
- `core/tests/platform/test_main_integration.py`
- `.agent-work/handoffs/T007-platform-wiring.md`

No DB engine, API router, other tests, manifests, lockfiles, desktop files,
GoalBuddy state, or other platform files were edited. No model/runtime/
inference/training/embedding/download, package install, venv, native build,
background thread, cache, telemetry, or filesystem scan was introduced.

## Changed files and modules

- `core/zana_core/main.py` - removed the direct `platformdirs` import and the
  duplicate `Path(platformdirs.user_data_dir(...)) / "db" / "zana.sqlite3"`
  construction. `create_app` now:
  - preserves `database_path=` behavior exactly: an explicit path wins and
    never resolves/creates platform roots;
  - accepts backward-compatible optional injection of a canonical
    `PlatformPaths` object or a `PathResolver` factory (keyword-only, no
    global mutable settings);
  - for production, resolves the full canonical root set via
    `PathResolver().resolve()` (or the injected factory), validates the full
    set, explicitly ensures only `PathRoot.DATA`, derives the safe child
    `data/db/zana.sqlite3` through `derive_child` (component/depth/length and
    symlink rules), then passes it to `Database`;
  - fails closed before DB migration when root/path validation fails
    (`PlatformPathError` propagates; no raw traceback, no cwd/home fallback);
  - makes no changes to token handling, CORS, routers, CLI, or server
    behavior.
- `core/tests/platform/test_main_integration.py` - focused integration tests:
  explicit `database_path` bypasses resolver/ensure with zero platform root
  creation; injected safe temp `PlatformPaths` creates only `data/db/
  zana.sqlite3` (config/cache/log/temp/workspace stay absent) and serves a
  real migrated SQLite database with the authenticated health contract;
  injected resolver factory is honored; unsafe alias and reversed
  parent/child root sets fail before any mutation with zero partial creation;
  `main.py` no longer imports or references `platformdirs`.

## Checks run and evidence

All commands used the existing shared environment at
`/Users/sero/Documents/zana/core/.venv/bin`; no dependencies were installed
and no venv was created.

| Check | Command | Result |
| --- | --- | --- |
| Platform + API tests | `core/.venv/bin/python -m pytest core/tests/platform core/tests/api -q` | 69 passed |
| Full Core suite | `core/.venv/bin/python -m pytest core/tests -q` (escalated for loopback/network) | 846 passed |
| Ruff lint | `core/.venv/bin/ruff check core/zana_core/main.py core/tests/platform/test_main_integration.py` | clean |
| Ruff format | `core/.venv/bin/ruff format --check core/zana_core/main.py core/tests/platform/test_main_integration.py` | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core/main.py` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

## Security delta

- Production DB path is now resolved and validated by the canonical platform
  boundary (unsafe root/home/cwd, alias, and parent/child rules) before any
  filesystem mutation or migration.
- Only the data root is created; config/cache/log/temp/workspace are never
  created by the app factory.
- The DB file path is derived with `derive_child`, rejecting traversal,
  separators, NUL bytes, depth/length overflows, and symlink escapes.
- Validation failures fail closed before migration and never expose private
  paths or raw tracebacks through the API; there is no fallback to cwd/home
  or a hard-coded host path.

## Residual risk

- Production now depends on the canonical platform package resolving a valid
  disjoint root set on the host; the strict validation is intentional and
  fail-closed.
- The `path_resolver_factory` injection is keyword-only and backward
  compatible; no global mutable settings were introduced.
- `Database` behavior (Alembic migrations, WAL/FK pragmas) is unchanged; the
  integration only changed how its default path is derived.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `565be1a` (`feat: wire canonical platform DB path boundary`)
  on branch `agent/T007-platform-wiring`, started exactly at integrated
  commit `601432f`.
- This handoff is committed separately on the same branch.
- Cherry-pick both commits onto the integration lane. No lockfiles, DB
  schema, API registration, desktop files, or GoalBuddy state are included.
  After integration, rerun the focused platform/API tests plus the full Core
  suite with loopback permission.
