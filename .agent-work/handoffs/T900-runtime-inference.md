# T900 Runtime Inference Handoff - Bounded Local Inference Adapters

Verdict: PASS

## Scope

Implemented the complete local inference adapter area over the canonical
runtime transport and the instance `InferenceAdapter` contract. Owned paths
only were touched: `core/zana_core/runtimes/inference.py`,
`core/zana_core/runtimes/ollama.py`,
`core/zana_core/runtimes/openai_compat.py`,
`core/zana_core/runtimes/transport.py`,
`core/zana_core/runtimes/__init__.py`,
`core/tests/runtimes/test_inference.py`, and this handoff. The lead review
expanded ownership to the instance contract files under
`core/zana_core/instances/` and their tests. No API/UI change, no dependency
install, no network use, and no runtime/model/thread/background-worker was
started.

## Changed files and modules

- `core/zana_core/runtimes/inference.py` - frozen `InferenceLimits` with hard
  context/message/output/token/body/stream/line/event/tool/stop/timeout caps;
  typed sanitized errors; bounded `LineBuffer` NDJSON/SSE line framer; shared
  `BaseRuntimeInferenceAdapter` implementing the instance `InferenceAdapter`
  protocol with parameter validation, cooperative cancellation, absolute
  deadline, exact model identity verification, bounded output accumulation,
  terminal-event normalization, and sanitized transport/protocol failures.
- `core/zana_core/runtimes/ollama.py` - `OllamaInferenceAdapter` posting
  bounded `/api/chat` NDJSON with deterministic generation settings
  (`temperature`, `num_predict`, `top_p`, `stop`) and parsing streamed
  content, done/failure events, and bounded tool calls.
- `core/zana_core/runtimes/openai_compat.py` -
  `OpenAICompatInferenceAdapter` posting bounded `/v1/chat/completions` SSE
  with deterministic generation settings and parsing `data:` deltas,
  `[DONE]`, finish reasons, error objects, and bounded tool calls.
- `core/zana_core/runtimes/transport.py` - additive `StreamTransport`
  protocol and `UrllibStreamTransport` with bounded chunk reads, the
  canonical 1 MiB cap, sanitized non-2xx/timeout errors, and deterministic
  response close via `_BoundedStream`; no existing `HttpTransport` contract
  changed.
- `core/zana_core/runtimes/__init__.py` - bounded re-exports of the new
  inference limits/errors, adapters, and streaming transport.
- `core/zana_core/instances/models.py` - `runtime_model_id` on `StartPlan`
  and `SessionBinding` as the strict runtime-native model identity.
- `core/zana_core/instances/runtime_selection.py` - sets `runtime_model_id`
  from `ModelDescriptor.model_id` while keeping composite `model_key`.
- `core/zana_core/instances/lifecycle.py` - verifies `runtime_model_id` in
  exact session binding matching.
- `core/tests/runtimes/test_inference.py` - 52 loopback-free tests using only
  injected in-memory protocol fixtures and injected clock/cancellation.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused inference pytest | `pytest core/tests/runtimes/test_inference.py -q` | 31 passed |
| All runtime pytest | `pytest core/tests/runtimes -q` | 151 passed |
| Relevant instance pytest | `pytest core/tests/instances -q` | 44 passed |
| Full Core pytest | `pytest core/tests -q` | 1619 passed |
| Ruff lint | `ruff check <owned files>` | clean |
| Ruff format | `ruff format --check <owned files>` | clean |
| Pyright | `pyright core/zana_core/runtimes` | 0 errors, 0 warnings |
| Import smoke | `import zana_core.runtimes; import zana_core.instances` | pass, no circular import |
| Diff hygiene | `git diff --check` | pass |

Test coverage includes: Ollama and OpenAI-compatible success (single and
multi-event), malformed JSON, truncated streams without terminal events,
oversize line/byte/output/event bounds, cancellation before and mid-stream,
timeout, non-2xx sanitization, secret non-leak in rendered results, exact
model identity mismatch, runtime error events, and bounded tool-call parsing;
no socket, loopback server, runtime, model, thread, or background worker is
used.

## Security delta

- Hard request/response/stream/event/output limits fail closed before or
  during any call; no unbounded accumulation or background workers.
- Exact model-key identity is verified against every reported model; mismatch
  becomes a typed `IDENTITY_MISMATCH` failure, never silent substitution.
- Raw exception text, HTTP bodies, bearer tokens, and runtime error messages
  are never surfaced; results carry stable sanitized messages.
- Non-2xx and transport failures map to typed `failed`/`timeout` results with
  recovery codes; partial output is never presented as verified completion.
- Streaming transport closes the response deterministically and reuses the
  canonical 1 MiB bounded response policy.

## Residual risk

- Live Ollama/OpenAI-compatible server behavior was not exercised per the
  strict no-runtime/no-network lane constraint; parsing is fixture-verified
  against documented wire shapes, and provider-version drift may require
  later fixture updates.
- Tool-call arguments now have dedicated byte and character caps in addition
  to the stream line/byte caps.
- `InferenceUnavailableError` is exported as the typed capability surface but
  is not yet raised by a caller; API/instance wiring is a later integration
  lane.

## Blockers

None.

## Review fix (lead review)

The lead review fix commit `5de21f4`
(`fix: runtime-native inference identity and bounded tool parsing`) adds the
following, with ownership expanded to the instance contract surface:

- `runtime_model_id` is now strict on `StartPlan` and `SessionBinding`;
  `RuntimeSelectionService` sets it from `ModelDescriptor.model_id`, and
  lifecycle binding matching verifies it. ZANA `model_key` stays composite
  (`runtime_id:model_id`) while Ollama/OpenAI requests send
  `binding.runtime_model_id`, and provider response identity is compared to
  that native id. A real-shape test proves `ollama-local:qwen2:1.5b` sends
  `qwen2:1.5b`.
- Adapters accept an explicit `runtime_id`; before any request they verify
  normalized exact endpoint and runtime id against `SessionBinding`, and a
  mismatch returns typed `IDENTITY_MISMATCH` without opening transport.
- OpenAI fragmented tool calls accumulate bounded per-index fragments
  (id/name/arguments) with total-tool, argument-byte, and argument-character
  caps; JSON objects are decoded only at the terminal event; malformed or
  incomplete fragments fail closed and never emit empty-argument calls.
  Ollama full tool calls are parsed fail-closed with the same caps.
- Request validation now bounds stop item/total bytes, endpoint, bearer-token
  size, finite/in-range timeout, and context/message bytes before JSON
  allocation/open; invalid UTF-8 stream bytes are rejected rather than
  replacement-decoded.
- Transport cleanup failure returns sanitized `INFERENCE_CLEANUP_FAILED`
  instead of overriding or surviving uncertain cleanup; `HTTPError` bodies
  close deterministically.
- OpenAI streaming requests advertise `text/event-stream`; Ollama keeps
  `application/x-ndjson`.

### Verification for the review fix

Host-safety rule applied: no broad full-suite, live, provider, browser, app,
device, bundle, runtime, model, inference, download, training, load,
GPU/RAM, container, or performance test was run, and no Ollama or local
model was started.

| Check | Result |
| --- | --- |
| Focused changed-file pytest: `tests/runtimes/test_inference.py`, `tests/instances/test_models.py`, `tests/instances/test_runtime_selection.py`, `tests/instances/test_lifecycle.py` | 78 passed |
| Ruff check on owned files | clean |
| Ruff format check on owned files | clean |
| Pyright `zana_core/runtimes` + `zana_core/instances` | 0 errors, 0 warnings |
| Import smoke | pass, no circular import |
| `git diff --check` | pass |

Intentionally not run under the host-safety rule: the full Core suite, the
broader all-runtime suite (including the pre-existing loopback transport
test), live provider/runtime/model inference, and any other broad or
performance verification. Earlier in this lane, before the rule was issued,
the full Core suite had passed 1641 tests; that result is retained as prior
evidence but was not rerun.

## Merge instructions

- Implementation commit: `f1b34b4`
  (`feat: add bounded local inference adapters`) on branch
  `agent/t900-runtime-inference`, started exactly at base
  `9c1dfb4a2ee4b30fe836d795ef4663e3d921bd75`.
- Review-fix commit: `5de21f4`
  (`fix: runtime-native inference identity and bounded tool parsing`).
- Receipt commit: this handoff commit (resolve with `git rev-parse HEAD`).
- This handoff is committed separately on the same branch.
- Merge `core/zana_core/runtimes/inference.py`,
  `core/zana_core/runtimes/ollama.py`,
  `core/zana_core/runtimes/openai_compat.py`,
  `core/zana_core/runtimes/transport.py`,
  `core/zana_core/runtimes/__init__.py`,
  `core/tests/runtimes/test_inference.py`, and this handoff through the PM
  integration lane. No lockfile, manifest, API, DB, or desktop file is
  included, and no existing runtime discovery contract was modified.

## Clean proof and remote state

- After the implementation commit: index clean, worktree clean,
  `git status --porcelain` empty, `git diff --check` pass.
- After the handoff commit: index clean, worktree clean,
  `git status --porcelain` empty, `git diff --check` pass.
- Remote/push: no push performed per delegated receipt contract; local HEAD
  on `agent/t900-runtime-inference` is the accepted receipt SHA, and
  `origin` remains unreconciled. Explicit push blocker: this lane was
  instructed to commit receipts locally only and never push.
