# T007 Permissions Handoff — Default-Deny Policy Engine

Verdict: PASS

## Scope

Implemented the production-grade default-deny permission policy for ZANA
capability sources as a pure module. No UI, database, API, OS keychain,
runtime, inference, or training surface was touched. Only owned paths were
changed: `core/zana_core/permissions/**`, `core/tests/permissions/**`,
`schemas/permissions.schema.json`, and this handoff.

## Changed files and modules

- `core/zana_core/permissions/models.py` — versioned typed policy with
  `schemaVersion`, network, filesystem read/write, tools, secrets, and
  experimental MCP sections. Omission resolves to deny-by-default: network
  `offline`/`outbound=false`, empty path/tool/secret allowlists, MCP endpoints
  disabled.
- `core/zana_core/permissions/loader.py` — safe YAML/dict loading with
  `yaml.SafeLoader`, duplicate-key rejection, unknown-field rejection,
  unsupported schema version rejection, forbidden tool id rejection, and
  structured recovery errors.
- `core/zana_core/permissions/decisions.py` — decision engine covering network
  offline/ask, built-in tool allowlist, secret-reference allowlist, filesystem
  containment under explicit mount roots, and disabled-by-default experimental
  MCP endpoints/scopes.
- `core/zana_core/permissions/redaction.py` — structured redaction helpers used
  by every denial so secret values and private document contents never appear
  in errors or loggable objects.
- `schemas/permissions.schema.json` — canonical JSON Schema aligned with the
  runtime Pydantic validation, including `additionalProperties: false` and
  deny-by-default values.
- `core/tests/permissions/**` — 31 focused tests covering default deny, each
  explicit allow, forbidden tool ids, traversal and symlink escape, offline
  network, unknown fields/schema versions, MCP disabled default, and
  redaction.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused pytest | `python -m pytest core/tests/permissions -q` | 31 passed |
| Full Core pytest | `python -m pytest core/tests -q` | 68 passed |
| Ruff lint | `ruff check core` | clean |
| Ruff format | `ruff format --check core` | clean |
| Pyright | `pyright core/zana_core` | 0 errors, 0 warnings |
| Schema parse | JSON load of `schemas/permissions.schema.json` | parse ok, `schemaVersion` const 1 |
| Diff hygiene | `git diff --check` | pass |

Verification used the existing shared Core virtualenv at
`/Users/sero/.codex/worktrees/216c/zana/core/.venv` with
`PYTHONPATH=/Users/sero/.codex/worktrees/ba7c/zana/core`; no dependencies were
installed in this worktree.

## Security delta

- Default deny everywhere: no outbound network, no filesystem roots, no tools,
  no secrets, no MCP endpoints unless explicitly listed.
- Capability sources cannot enable shell, Python execution, install scripts,
  post-install hooks, or arbitrary code; such tool ids are rejected at load.
- Unknown fields and unsupported schema versions fail closed with recovery
  errors instead of being ignored.
- Filesystem decisions resolve symlinks and `..` segments and reject any
  resolved path outside the explicit mount roots.
- Denials are structured and redacted; secret values and private document
  contents never enter error messages or loggable objects.
- The package is pure: no DB/API/OS-keychain integration in this lane.

## Residual risk

- The built-in tool registry currently contains only `zana.calculator`;
  future trusted tools must be added deliberately and never collide with the
  forbidden names.
- Symlink resolution uses `Path.resolve`; very exotic mount/fuse edge cases are
  not separately tested on non-macOS platforms.
- This lane provides policy representation and decisions only; runtime
  enforcement wiring into builds/instances is a later lane and must call these
  primitives rather than duplicating checks.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `183b522`
  (`feat: add default-deny permission policy engine`) on branch
  `agent/T007-permissions`, started exactly at integrated commit `9e36e4c`.
- This handoff is committed separately on the same branch.
- Merge `core/zana_core/permissions/**`, `core/tests/permissions/**`,
  `schemas/permissions.schema.json`, and this handoff through the PM
  integration lane. No lockfile or manifest change is included.
