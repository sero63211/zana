# T900 Runtime Native Tools Handoff - Native Tool Schemas and Continuation

Verdict: PASS

## Scope

Implemented the complete backward-compatible native tool-call
request/continuation interface over the owned runtime inference adapters.
Owned paths only were changed: `core/zana_core/runtimes/inference.py`,
`core/zana_core/runtimes/ollama.py`,
`core/zana_core/runtimes/openai_compat.py`,
`core/tests/runtimes/test_inference.py`,
`core/tests/runtimes/test_native_tools.py`, and this handoff. No API, DB,
manifest, desktop, build, instance, permission, or tool-execution code was
modified, and no dependency was installed.

## Changed files and modules

- `core/zana_core/runtimes/inference.py` - hard `InferenceLimits` for native
  tool definitions and tool results; typed fail-closed
  `ToolDefinitionsError`/`ToolContinuationError`/`ToolResultLimitError`;
  deterministic bounded provider-safe `zana_<index>` aliases with
  collision checks; exact `native_tool_schema` serialization of provider
  alias/description/JSON parameters only; request-local alias maps threaded
  through request building and stream draining, with parsed provider aliases
  mapped back to canonical tool ids and unknown/undeclared aliases failing
  closed; strict JSON serialization with `allow_nan=False` and separate
  serialized character versus UTF-8 byte bounds for definitions, prior
  arguments, provider-returned arguments, and tool results;
  duplicate/invalid/oversized definition and continuation validation;
  canonical OpenAI/Ollama message builders using the order
  `system -> user -> assistant tool_calls -> tool messages`; optional
  `tool_definitions`/`tool_requests`/`tool_results` inputs defaulting to
  empty so behavior-only callers remain byte-compatible.
- `core/zana_core/runtimes/ollama.py` - sends native `tools` only when
  definitions are explicitly supplied and canonical Ollama assistant/tool
  continuation messages using provider aliases; maps returned alias tool
  names back to canonical ids; transport/parsing/identity/bounds unchanged.
- `core/zana_core/runtimes/openai_compat.py` - sends native `tools` only
  when definitions are explicitly supplied and canonical OpenAI
  assistant/tool continuation messages using provider aliases; maps
  returned alias tool names back to canonical ids;
  transport/parsing/identity/bounds unchanged.
- `core/tests/runtimes/test_inference.py` - existing focused tool parsing
  tests updated to the alias contract: returned tool calls require a
  declared provider alias, and malformed/oversize argument tests use a
  declared definition.
- `core/tests/runtimes/test_native_tools.py` - 41 focused injected-transport
  tests covering exact byte-compatible behavior-only requests, exact native
  schemas with provider-safe aliases, alias round-trip to canonical ids,
  no-declaration tool calls failing closed, unknown/collision alias
  fail-closed, duplicate/invalid/non-JSON/oversize definitions, multibyte
  character-vs-byte boundaries on both request and provider-returned
  arguments, NaN/Infinity rejection for complete and streamed calls,
  continuation role order, exact continuation bytes, matching/count/
  undeclared-tool failures, non-JSON tool-result failures, cancellation,
  and no adapter-side tool execution.

## Checks run and evidence

Host-safety rule applied: only the smallest focused tests plus bounded
static/type/lint/format/import/diff checks ran. No live runtime, model,
network, inference, install, broad suite, app, browser, or build ran, and no
runtime/model was started.

| Check | Command | Result |
| --- | --- | --- |
| Focused inference + native-tool pytest | `pytest core/tests/runtimes/test_inference.py core/tests/runtimes/test_native_tools.py -q` | 101 passed |
| Ruff lint | `ruff check` on the five owned files | clean |
| Ruff format | `ruff format --check` on the five owned files | clean |
| Pyright | `pyright` on the three runtime files and `test_native_tools.py` | 0 errors, 0 warnings |
| Import smoke | `import zana_core.runtimes; import zana_core.runtimes.ollama; import zana_core.runtimes.openai_compat; import zana_core.instances` | pass, no circular import |
| Diff hygiene | `git diff --check` | pass |

Verification reused the existing shared Core virtualenv at
`/Users/sero/Documents/zana/core/.venv` with
`PYTHONPATH=/Users/sero/.codex/worktrees/4a35/zana/core`; no dependencies
were installed.

## Security delta

- Tool definitions are optional exact trusted `ToolDefinition` records;
  callable/code-bearing and non-JSON-serializable schema data fail closed
  before any transport call.
- Native schemas serialize only a bounded deterministic provider-safe alias,
  `description`, and JSON `parameters`; canonical ids, tool version, and code
  fields never cross the runtime boundary.
- Provider aliases are request-local (`zana_<index>`, max 64 chars/bytes,
  `[A-Za-z0-9_-]` only), collision-checked, serialized in schemas and
  assistant/tool continuations, and mapped back to exact canonical tool ids
  before any `ToolRequest` is returned. Unknown or undeclared aliases fail
  closed as `TOOL_ALIAS_INVALID`; a provider tool call with zero supplied
  definitions also fails closed and never becomes a `ToolRequest`.
- Duplicate definitions, undeclared/out-of-order continuations, count
  mismatches, and oversized schemas/results fail closed with typed sanitized
  error codes.
- Character limits are measured on the serialized JSON string and byte limits
  on its UTF-8 encoding, so multibyte schemas are bounded honestly.
- Provider-bound definition/argument/result JSON uses strict serialization
  (`allow_nan=False`) and rejects NaN/Infinity returned by providers before
  a `ToolRequest` is emitted.
- Tool-result continuation is accepted only as exact canonical
  `ToolRequest`/`ToolResult` pairs with matching declared tool ids.
- The adapter only transports and parses; it never resolves, permits, or
  executes a tool. Exact endpoint/runtime/model binding, cancellation,
  deadlines, streaming, output/request bounds, and sanitized errors are
  preserved.
- Behavior-only requests remain byte-compatible with the prior adapters.

## Residual risk

- No live Ollama/OpenAI-compatible server, model, network, or inference was
  exercised under the host-safety policy; provider-version drift may require
  later fixture updates.
- Chat orchestration wiring from permission-gated execution back into the
  adapter is a later integration lane; this scope supplies the adapter
  request/continuation contract only.

## Blockers

None.

## Merge instructions

- Product commit: `0083cb4869922bf291ef0d22e380a4c43beb949d`
  (`feat: native tool schemas and bounded tool-result continuation`) on
  branch `agent/t900-runtime-native-tools`, started exactly at canonical base
  `23b9034df5c91b19b86a98fba210d1712ba564e6`.
- Lead correction commit: `c79c78bae8c8418cd80bd5353ade45fbb6a5ad78`
  (`fix: provider-safe tool aliases and strict native JSON bounds`).
- Receipt commit: this handoff commit (resolve with `git rev-parse HEAD`).
- Merge the five owned product/test files and this handoff through the PM
  integration lane. No lockfile, manifest, API, DB, desktop, or shared
  contract file is included.

## Clean proof and remote state

- After the product commit: index clean, worktree clean, `git status
  --porcelain` empty, `git diff --check` pass.
- After the correction commit: index clean, worktree clean, `git status
  --porcelain` empty, `git diff --check` pass.
- After the receipt commit: index clean, worktree clean, `git status
  --porcelain` empty, `git diff --check` pass.
- Remote/push: no push performed per delegated receipt contract; local HEAD
  on `agent/t900-runtime-native-tools` is the accepted receipt SHA, and
  `origin` remains unreconciled. Explicit push blocker: this lane was
  instructed to commit receipts locally only and never push.
