# T007 Tools Handoff — Minimal Trusted Built-In Tool Boundary

Verdict: PASS

## Scope

Implemented the minimal trusted built-in tool boundary for ZANA. The package
never executes shell, Python, filesystem, network, MCP, plugin, or user code.
Only owned paths were changed: `core/zana_core/tools/**`,
`core/tests/tools/**`, and this handoff. Integrated permission contracts were
reused read-only; no shared module was modified.

## Changed files and modules

- `core/zana_core/tools/models.py` — strict immutable `ToolDefinition`,
  `ToolCall`, `ToolResult`, `ToolError`, `ToolErrorCode`,
  `ToolExecutionProvenance`, and `ToolStatus`.
- `core/zana_core/tools/calculator.py` — real safe calculator using a small
  parsed AST grammar with approved operators only; never `eval`/`exec`.
  Supports numeric literals, parentheses, unary +/- and `+ - * /`; rejects
  names, attributes, calls, indexing, comprehensions, strings, containers,
  imports, lambdas, exponent/resource bombs, division by zero, non-finite
  results, excessive length/depth/node count/integer digits/magnitude.
- `core/zana_core/tools/registry.py` — registry containing only code-owned
  adapters; unknown tools fail closed.
- `core/zana_core/tools/executor.py` — `PermissionGatedToolExecutor` evaluates
  the integrated default-deny permission policy for the exact image/instance
  context before resolving or invoking an adapter. Unknown tools fail closed,
  denied calls never execute, and decision/reason are recorded.
- `core/tests/tools/**` — 33 focused tests covering permission check before
  adapter call, default deny, allowed calculation, precedence/unary/decimal
  cases, every prohibited AST class, bombs/limits, zero division,
  non-finite/magnitude, unknown tool, malformed input, deterministic
  output/digests, and redacted provenance.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused pytest | `python -m pytest core/tests/tools -q` | 33 passed |
| Full Core pytest | `python -m pytest core/tests -q` | 503 passed, 1 unrelated pre-existing hardware probe failure (`test_darwin_metal_real_probe`, not owned surface) |
| Ruff lint | `ruff check core` | clean |
| Ruff format | `ruff format --check core` | clean |
| Pyright | `pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

Verification reused the existing shared Core virtualenv at
`/Users/sero/.codex/worktrees/216c/zana/core/.venv` with
`PYTHONPATH=/Users/sero/.codex/worktrees/ba7c/zana/core`; no dependencies were
installed and no model/runtime was started.

## Security delta

- Default-deny permission evaluation occurs before tool resolution/adapter
  invocation; denied calls never execute.
- Registry is code-owned only; no manifest path, imported source, model text,
  or arbitrary name can register executable code.
- Calculator rejects every prohibited AST class and resource bomb; no shell,
  Python execution, filesystem, network, MCP, plugin install, dynamic import,
  or user code.
- Provenance stores call id, tool id/version, permission decision, normalized
  input digest, status, result digest/error code, timestamps/duration; it
  never logs secrets or entire private prompts.
- Duration metadata is emitted only as an actually measured monotonic
  timestamp delta.

## Residual risk

- The full-suite hardware probe failure is environmental to this host and
  unrelated to the owned surface; it is reported for transparency and should
  not block this handoff.
- The calculator relies on CPython AST parsing; a future hardened grammar
  implementation could remove that dependency, but no execution primitive is
  reachable today.
- Tool-loop integration (chat/model tool requests) is a later lane and must
  call `PermissionGatedToolExecutor` rather than bypassing it.

## Blockers

None.

## Commit and cherry-pick instructions

- Implementation commit: `fc4cb64`
  (`feat: add trusted built-in tool boundary`) on branch `agent/T007-tools`,
  started exactly at integrated commit `13426e5`.
- This handoff is committed separately on the same branch.
- Cherry-pick `fc4cb64` and the handoff commit onto the PM integration branch
  in that order. No lockfile, manifest, schema file, or other lane path is
  included.
