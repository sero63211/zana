# T007 Runtime Discovery Handoff — Safe Local Runtime and Model Probes

Verdict: PASS

## Scope

Implemented the T007 runtime/model discovery lane for the frozen T005
contracts. Only owned paths were touched: `core/zana_core/runtimes/**`,
`core/tests/runtimes/**`, and this handoff. No DB/API registration changes,
no interface-contract changes, no dependency installation, and no runtime or
model processes were started.

## Changed files and modules

- `core/zana_core/runtimes/base.py` — canonical `RuntimeDescriptor`,
  `ModelDescriptor`, `ProbeTarget`, `HttpResponse`, transport/adapter
  protocols, probe/endpoint/response errors, and `PullApproval`.
- `core/zana_core/runtimes/transport.py` — stdlib `urllib` transport with
  short bounded timeouts, 1 MiB response limit, no credential logging.
- `core/zana_core/runtimes/endpoints.py` — manual endpoint validation:
  http(s) only, embedded-credential rejection, no auto adapter, no probing.
- `core/zana_core/runtimes/executables.py` — safe `shutil.which` checks for
  `ollama`, `lms`, `llama-server`, and `mlx_lm`.
- `core/zana_core/runtimes/ollama.py` — `/api/tags` plus per-model `/api/show`
  enrichment; digest-strength identity; explicit user-approved native pull
  planner and pure pull-event parser that never execute or proxy model bytes.
- `core/zana_core/runtimes/openai_compat.py` — generic `/v1/models` adapter
  with optional bearer header.
- `core/zana_core/runtimes/lmstudio.py` — LM Studio identification only when
  `/api/v0/models` evidence responds; otherwise generic OpenAI-compatible.
- `core/zana_core/runtimes/llamacpp.py` — llama.cpp identification only via
  `/props` identity evidence; otherwise generic OpenAI-compatible.
- `core/zana_core/runtimes/mlx_server.py` — MLX-LM identification only via
  `/version` evidence, with the required development-server warning.
- `core/zana_core/runtimes/registry.py` — bounded concurrent localhost probe
  registry with default localhost candidates only; no LAN scanning.
- `core/tests/runtimes/**` — 29 focused tests using injected fake transports
  and a real bounded loopback `http.server` protocol server.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused pytest | `core/.venv/bin/python -m pytest core/tests/runtimes -q` | 29 passed |
| Full Core suite | `core/.venv/bin/python -m pytest core/tests -q` | 66 passed |
| Ruff lint | `core/.venv/bin/ruff check core` | clean |
| Ruff format | `core/.venv/bin/ruff format --check core` | clean |
| Pyright | `core/.venv/bin/pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

Test coverage includes: Ollama unavailable, empty, real-shaped metadata,
invalid response; generic OpenAI-compatible manual endpoint; bounded timeout;
executable present/server off; digest vs weak identity; LM Studio, llama.cpp,
and MLX-LM evidence-based identification; and a real localhost transport.

## Interface facts

- New package is internal to Core and does not change T005 API, DB, domain,
  or job contracts. Integration will wire `RuntimeProbeRegistry` results into
  the authenticated runtime/models API in a later lane.
- Manual endpoints require an explicit adapter and reject embedded
  credentials. No automatic remote discovery occurs.
- Invalid known-port responses always produce `registered: false` with an
  honest `ERROR`/`OFFLINE` descriptor; executable-present/server-off is
  reported as installed and not running.
- Ollama pull planning is gated by a `PullApproval` object; no network call,
  subprocess, model bytes, or download is performed by this lane.

## Security delta

- Probes are localhost-only for defaults, bounded by short timeouts and a 1 MiB
  response cap.
- No LAN scanning, process injection, or auto-start of third-party runtimes.
- Endpoint credentials embedded in URLs are rejected; bearer tokens are passed
  only as headers and never logged or persisted.
- Unknown metadata remains null; identity strength is reported honestly.

## Residual risk

- Provider-specific metadata endpoints (`/api/v0/models`, `/props`,
  `/version`) vary by runtime version; the adapters fall back to generic
  OpenAI-compatible discovery when evidence is absent or invalid.
- The registry currently returns descriptors only; DB persistence and API
  registration remain the integration lane's responsibility.
- Transport tests bind loopback sockets; they require network permission in
  sandboxed environments (covered by the verification evidence above).

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `8f26220` (`feat: add runtime and model discovery adapters`)
  on branch `agent/T007-runtimes`, started exactly at integrated commit
  `9e36e4c`.
- This handoff is committed separately on the same branch.
- Merge `core/zana_core/runtimes/**`, `core/tests/runtimes/**`, and this
  handoff through the PM integration lane. No DB schema, migration, API
  registration, lockfile, or desktop files are included.
