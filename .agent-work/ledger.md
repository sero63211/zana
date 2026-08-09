# ZANA Coordination Ledger

`docs/goals/zana-mvp/state.yaml` is authoritative board truth. This ledger is
operational coordination only and never supersedes it.

## Conventions

- One row per task; update state as it changes.
- Before a writer starts, record/verify canonical repo, HEAD, branch, remote,
  clean status, worktree, and exclusive owner; never share a writer worktree.
- Shared contracts have a single owner/integrator; that owner is recorded in
  the row for the owning task.
- Stop and escalate before changing an interface contract; do not edit another
  agent's owned paths.
- Keep notifications quiet: blockers, interface changes, and completion only.
- Verification column records the exact commands or evidence required for
  `done`.
- An accepted row receipt includes focused commit SHA, changed paths, gates,
  security delta, residual risk, clean index/worktree proof, and confirmed
  push SHA/remote state or an explicit push blocker.
- Known red gates are repaired before commit/push/next scope. Pushes are
  fetch/reconcile first, non-force, and never claimed without remote proof.
- Current host-safety override permits only the smallest focused unit tests and
  bounded static/type/lint/format checks. Broad/full suites and all live API,
  provider, browser, device, app, bundle, runtime, model, download, inference,
  training, load, GPU/RAM, container, and performance checks are deferred.
- Do not delete or rewrite another agent's row, branch/worktree, or files.

## Rows

| task | agent | branch/worktree | owned paths | state | dependencies | verification |
| --- | --- | --- | --- | --- | --- | --- |
| T001 | Judge | none (read-only) | `docs/goals/zana-mvp/notes/` | done | controlling ZANA build plan, Appoint Me agent docs, installed router catalog | receipt `PASS`; note `T001-spec-architecture-judge.md` |
| T002 | PM | none | GoalBuddy control files | done | T001 receipt | state.yaml updated; `next_allowed_task: T003` |
| T003 | Worker | none (foundation, Git initialized without commit) | `AGENTS.md`, `docs/agent-adoption-map.md`, `.agent-work/**`, `.gitignore` | done | T001 note, `23_AGENT_ORCHESTRATION.md`, Appoint Me AGENTS.md, handoff protocol | `test -f` all required files; `rg` adoption/verdict/profile/authority terms; `git diff --check` |
| T004-core | `router_opencode_go_deepseek_v4_pro` | `agent/T004-core` at `/private/tmp/zana-worktrees/T004-core` | `README.md`, root workspace manifests except lockfiles, `core/**`, `scripts/**`, `.github/**`, `T004-core.md` | done | T003 handoff; fixed bearer-token/health contract in state.yaml | 7 pytest; ruff; pyright; handoff PASS |
| T004-desktop | `router_opencode_go_deepseek_v4_flash` then PM recovery | `agent/T004-desktop` at `/private/tmp/zana-worktrees/T004-desktop` | `apps/desktop/**`, `T004-desktop.md` | done | T003 handoff; fixed invoke/sidecar/bootstrap contract in state.yaml | ESLint; TypeScript; 3 Vitest; Vite; Cargo fmt/clippy; handoff PASS |
| T004-integration | PM | `master` at `/Users/sero/Documents/zana` | lockfiles, this ledger, `T004.md`, merges and cross-lane verification | done | T004-core and T004-desktop handoffs | frozen sidecar 200/401/401; `ZANA.app` bundle; healthy UI launch; clean exit |
| T005-contracts | visible Codex task `router_opencode_go_deepseek_v4_flash`, thread `019fe603-86a7-7ee2-a8b3-15981062570e`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a` | `agent/T005-contracts-flash` at `/Users/sero/.codex/worktrees/216c/zana` | Core pyproject + Alembic config/migrations, `core/zana_core/domain/**`, `db/**`, `jobs/**`, backend API/schema integration, focused tests, `T005.md` | done, PASS; integrated as `e22cba9` and `0ff5db1` | T004 PASS; specs 03/08/09/13/18/19 | 37 pytest; Ruff; Pyright; fresh migration; WAL/FK inspection |
| T006-ui | visible Codex task `opencode-go/deepseek-v4-flash` | `agent/T006-ui`, thread `019fe603-86a7-7ee2-a8b3-15981062570e` | `apps/desktop/src/**`, `T006-ui.md` | done | frozen React/Tauri architecture; T004 health contract | commit `c957389`; `git diff --check`; heavy gates deferred for host safety |
| T006-docs | visible Codex task `gpt-5.6-terra` | isolated Codex worktree, thread `019fe60d-b3d2-79b0-b44a-152cbdb2fb03` | `README.md`, `docs/product/**`, `T006-docs.md` | done | T006-ui receipt and current implementation evidence | commits `4b101d0`, `3e672ac`; `git diff --check` |
| T007-runtimes | `router_opencode_go_deepseek_v4_flash`, thread `019fe603-86a7-7ee2-a8b3-15981062570e`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-runtimes` at `/Users/sero/.codex/worktrees/216c/zana` | `core/zana_core/runtimes/**`, `core/tests/runtimes/**`, `T007-runtimes.md` | done, PASS; integrated `66b725a`, `8f9131e` | T005 PASS; spec 06 | 29 focused pytest; full suite; Ruff/Pyright |
| T007-hardware | `router_opencode_go_deepseek_v4_flash`, thread `019fe60d-b3d2-79b0-b44a-152cbdb2fb03`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-hardware` at `/Users/sero/.codex/worktrees/d795/zana` | `core/zana_core/hardware/**`, `core/tests/hardware/**`, `T007-hardware.md` | done, PASS; integrated `ac164f7`, `fa471c3` | T005 PASS; spec 07 | 54 focused pytest; 91 full suite; Ruff/Pyright |
| T007-capabilities | `router_opencode_go_deepseek_v4_flash`, thread `019fe5f7-08a9-7972-baaa-95df844cb977`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-capabilities` at `/Users/sero/.codex/worktrees/9529/zana` | `core/zana_core/capabilities/**`, `core/tests/capabilities/**`, capability/evaluation schemas, `T007-capabilities.md` | done, PASS; integrated `215055d`, `230bbaf` | T005 PASS; spec 09 | 75 focused pytest; integrated focused PASS; Ruff/Pyright |
| T007-artifacts | `router_opencode_go_deepseek_v4_flash`, thread `019fe65a-7992-7081-9d8a-3bef3f41e58b`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-artifacts` at `/Users/sero/.codex/worktrees/b8bd/zana` | `core/zana_core/artifacts/**`, `core/tests/artifacts/**`, `T007-artifacts.md` | done, PASS; integrated `e5c9ca5`, `89b5dce` | T005 PASS; specs 08/17 | 35 focused pytest; 72 full suite; Ruff/Pyright |
| T007-permissions | `router_opencode_go_deepseek_v4_flash`, thread `019fe65b-1ebf-7132-b240-e6b98772bb56`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-permissions` at `/Users/sero/.codex/worktrees/ba7c/zana` | `core/zana_core/permissions/**`, `core/tests/permissions/**`, permission schema, `T007-permissions.md` | done, PASS; integrated `347b04a`, `11b7d42` | T005 PASS; spec 16 | 31 focused pytest; 68 full suite; Ruff/Pyright |
| T007-knowledge | `router_opencode_go_deepseek_v4_flash`, thread `019fe603-86a7-7ee2-a8b3-15981062570e`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-knowledge` at `/Users/sero/.codex/worktrees/216c/zana` | `core/zana_core/knowledge/**`, `core/tests/knowledge/**`, `T007-knowledge.md` | done, PASS; integrated `29262c8`, `4e8e79c` | T007 runtime/artifact PASS; spec 10 | 26 focused pytest; Ruff/Pyright; no embeddings/models started |
| T007-evaluation | `router_opencode_go_deepseek_v4_flash`, thread `019fe65b-1ebf-7132-b240-e6b98772bb56`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-evaluation` at `/Users/sero/.codex/worktrees/ba7c/zana` | `core/zana_core/evaluation/**`, `core/tests/evaluation/**`, `T007-evaluation.md` | done, PASS; integrated `48e4081`, `b70c94c` | T005 PASS; spec 12 | 39 focused pytest; 0 Ruff/Pyright findings; deterministic scorers/gates |
| T007-images | `router_opencode_go_deepseek_v4_flash`, thread `019fe65a-7992-7081-9d8a-3bef3f41e58b`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-images` at `/Users/sero/.codex/worktrees/b8bd/zana` | `core/zana_core/images/**`, `core/tests/images/**`, image schema, `T007-images.md` | done, PASS; integrated `75bc24e`, `b6d250b` | artifact/permission PASS; specs 08/17 | 46 focused pytest; OCI digest/round-trip/corruption/traversal; honest zstd unavailable state |
| T007-memory | `router_opencode_go_deepseek_v4_flash`, thread `019fe60d-b3d2-79b0-b44a-152cbdb2fb03`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-memory` at `/Users/sero/.codex/worktrees/d795/zana` | `core/zana_core/memory/**`, `core/tests/memory/**`, `T007-memory.md` | done, PASS; integrated `b546388`, `08b48d4` | T005 PASS; spec 15 | 55 focused pytest; Ruff/Pyright; explicit reset/update/rollback |
| T007-planning | `router_opencode_go_deepseek_v4_flash`, thread `019fe5f7-08a9-7972-baaa-95df844cb977`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-planning` at `/Users/sero/.codex/worktrees/9529/zana` | `core/zana_core/planning/**`, `core/tests/planning/**`, `T007-planning.md` | done, PASS; integrated `4c199ce`, `14175bf` | capability/hardware/runtime PASS; specs 07/11/13 | 62 focused pytest; deterministic conservative estimates/approvals; no training/runtime execution |
| T007-training | `router_opencode_go_deepseek_v4_flash`, thread `019fe65b-1ebf-7132-b240-e6b98772bb56`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-training` at `/Users/sero/.codex/worktrees/ba7c/zana` | `core/zana_core/training/**`, `core/tests/training/**`, `T007-training.md` | done, PASS; integrated `5abb2cc`, `3b91dcf` | capability/evaluation/hardware PASS; spec 11 | 44 focused pytest; metadata-only provider probes; no training/model load |
| T007-builds | `router_opencode_go_deepseek_v4_flash`, thread `019fe603-86a7-7ee2-a8b3-15981062570e`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-builds` at `/Users/sero/.codex/worktrees/216c/zana` | `core/zana_core/builds/**`, `core/tests/builds/**`, `T007-builds.md` | done, PASS; integrated `9110842`, `48aabc4` | jobs/capability/knowledge/evaluation PASS; spec 13 | 28 focused pytest; pure lifecycle/cancellation/finalization contracts; no heavy phase execution |
| T007-hardware-test | `router_opencode_go_deepseek_v4_flash`, thread `019fe60d-b3d2-79b0-b44a-152cbdb2fb03`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-hardware-test` at `/Users/sero/.codex/worktrees/d795/zana` | `core/tests/hardware/test_providers.py`, `T007-hardware-test.md` | done, PASS; integrated `1cd4f32`, `544cb49` | integrated hardware lane | root cause proven; exact order + 54 hardware tests; integrated full Core 499 PASS |
| T007-instances | `router_opencode_go_deepseek_v4_flash`, thread `019fe65a-7992-7081-9d8a-3bef3f41e58b`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-instances` at `/Users/sero/.codex/worktrees/b8bd/zana` | `core/zana_core/instances/**`, `core/tests/instances/**`, `T007-instances.md` | done, PASS; integrated `5a95b28`, `76e67e3`; format alignment `e887c25` | runtime/image/memory/knowledge/permission PASS | 44 integrated focused pytest; Ruff/Pyright; exact identity + permission/provenance; injected adapters only |
| T007-tools | `router_opencode_go_deepseek_v4_flash`, thread `019fe65b-1ebf-7132-b240-e6b98772bb56`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-tools` at `/Users/sero/.codex/worktrees/ba7c/zana` | `core/zana_core/tools/**`, `core/tests/tools/**`, `T007-tools.md` | done, PASS; integrated `da7f837`, `99f5b23`, `1d70129`, `dbdb0c6` | permission PASS; specs 14/16 | 37 focused pytest; safe bounded AST calculator; default-deny; no shell/network/filesystem |
| T007-embeddings | `router_opencode_go_deepseek_v4_flash`, thread `019fe603-86a7-7ee2-a8b3-15981062570e`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-embeddings` at `/Users/sero/.codex/worktrees/216c/zana` | exact new knowledge embedding/retrieval files and tests, `T007-embeddings.md` | done, PASS; integrated `5f6a8ab`, `ed5ea21` | runtime + knowledge PASS; spec 10 | 22 focused pytest; bounded real Ollama adapter contract; no model start; LanceDB explicitly pending |
| T007-portability | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe78f-c167-7b83-924b-a533cf01ed6b`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | detached recovery worktree `/Users/sero/.codex/worktrees/438c/zana`, source `agent/T007-portability@17fe218` | portability package/tests plus image archive/OCI/import/secrets/models files and image tests, `T007-portability.md` | done, PASS reference `863a84e`, receipt `5eec7d2`; divergent history, reference only | artifact/image PASS; specs 08/17; strict lightweight owner constraint | 224 focused tests before final cleanup; Ruff/format/Pyright; final truthiness/dead-guard cleanup; `models.py` interface delta requires fresh canonical integration; no broad merge/cherry-pick |
| T007-resources | `router_opencode_go_deepseek_v4_flash`, thread `019fe5f7-08a9-7972-baaa-95df844cb977`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-resources` at `/Users/sero/.codex/worktrees/9529/zana` | `core/zana_core/resources/**`, `core/tests/resources/**`, `T007-resources.md` | done, PASS; integrated `0d3829f`, `ae5b41d` | hardware/planning PASS; strict lightweight owner constraint | 58 focused pytest; full branch 619 PASS; Ruff/Pyright; bounded leases/admission; zero background threads |
| T007-diagnostics | `router_opencode_go_deepseek_v4_flash`, thread `019fe603-86a7-7ee2-a8b3-15981062570e`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-diagnostics` at `/Users/sero/.codex/worktrees/216c/zana` | `core/zana_core/diagnostics/**`, `core/tests/diagnostics/**`, `T007-diagnostics.md` | done, PASS; integrated `a93b32f`, `f6f5737` | runtime/hardware/artifact/image PASS | 22 focused pytest; Ruff/Pyright; read-only bounded doctor; no model/service start |
| T007-observability | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe7e7-0b58-7a33-9849-0ca5a797e511`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-observability-integration-retry` at `/Users/sero/.codex/worktrees/7fac/zana` | observability package/tests plus canonical `streaming/redaction.py` and its test, `T007-observability-integration-retry.md` | done, PASS; integrated `ea31705`, receipt `27ecbb9`, pushed | security/logging spec 16; integrated streaming boundary | 226 focused tests; Ruff/format/Pyright; bounded local sinks/redaction; no telemetry/background/unbounded retention |
| T007-acquisition | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe603-86a7-7ee2-a8b3-15981062570e`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-acquisition` at `/Users/sero/.codex/worktrees/216c/zana` | `core/zana_core/acquisition/**`, `core/tests/acquisition/**`, `T007-acquisition.md` | done, PASS; integrated `8eaefc5` | runtime/resource PASS; specs 06/07 | 83 focused pytest; Ruff check/format; Pyright; native runtime pull only, streaming progress, preflight disk lease, bounded cancellation/deadline, no byte proxy |
| T007-platform | `router_opencode_go_deepseek_v4_flash`, thread `019fe5f7-08a9-7972-baaa-95df844cb977`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-platform` at `/Users/sero/.codex/worktrees/9529/zana` | `core/zana_core/platform/**`, `core/tests/platform/**`, `T007-platform.md` | done, PASS; integrated `a62bfe2`, `9797f61`, `dd77104`, `852fb18` | strict lightweight portability rule; spec 21 | 46 integrated focused pytest; Ruff/Pyright; strict final root-set validation; bounded probes |
| T007-streaming | `router_opencode_go_deepseek_v4_flash`, thread `019fe65a-7992-7081-9d8a-3bef3f41e58b`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-streaming` at `/Users/sero/.codex/worktrees/b8bd/zana` | `core/zana_core/streaming/**`, `core/tests/streaming/**`, `T007-streaming.md` | done, PASS; integrated `8282422`, `5d918e2` | jobs/API and chat/acquisition stream contracts; specs 18/22 | 50 integrated focused pytest; Ruff/Pyright; bounded resumable SSE; zero polling/background/event accumulation |
| T007-jobs-sse | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe827-beb6-7b92-bd3e-bca20dda1198`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-jobs-sse-canonical-integration` at `/Users/sero/.codex/worktrees/715b/zana`, source reference `agent/T007-jobs-sse@2dd18db` | exact jobs API/repository/service/test files, `T007-jobs-sse-canonical-integration.md` | done, PASS; integrated `c75e89b`, receipt `0c63112`, pushed | canonical streaming/observability boundary; persisted jobs | 83 focused API/jobs tests + 6 main integration tests; Ruff/format/Pyright; authenticated bounded Last-Event-ID SSE snapshot; paginated DB reads; no polling/background |
| T007-portability-canonical-integration | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe827-beb6-7b92-bd3e-bca20dda1198`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t007-portability-canonical-715b` at `/Users/sero/.codex/worktrees/715b/zana`, base `0c63112`, reference `863a84e` | portability package/tests; exact image archive/OCI/import/secrets/models/init files; image tests; exact immutable-fixture compatibility file; canonical handoff | done, PASS; worker `7cc2233`/`0e2497f`; integrated and pushed `f4d58e9` | canonical image/artifact/permission contracts; strict serialization and immutable nested-model compatibility | worker 227 focused + 1588 full; lead 235 focused + 1588 full; Ruff/format/Pyright/import/diff/security PASS; no live runtime/model/network/training |
| T900-api-system-runtime | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe868-40ad-7cd3-b026-5980b0d49978`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-api-system-runtime` at `/Users/sero/.codex/worktrees/90b6/zana` | single-owner system/runtime/model API boundary and exact tests/handoff | done, PASS; integrated/pushed `8342881` | canonical T007 domain/services | lead 96 focused + 1611 full Core; Ruff/format/Pyright/diff; auth, exact identity, savepoint, pruning, approval and route review PASS; no model start/download |
| T900-runtime-inference | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe870-59d1-74f2-98b2-13d3d0dd9561`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-runtime-inference` at `/Users/sero/.codex/worktrees/2aa0/zana` | exact runtime inference adapter files/tests/handoff | active | canonical runtime transport + instance inference protocol | bounded real Ollama/OpenAI-compatible inference code; fixture-only tests; no live model/server |
| T900-knowledge-providers | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe870-ce4c-7c71-8e2b-afe450a0a6f3`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-knowledge-providers` at `/Users/sero/.codex/worktrees/934f/zana` | exact Docling/LanceDB adapter files/tests/handoff | done, PASS; integrated/pushed `d84c22f` | canonical knowledge models/limits | lead 6 exact provider-contract tests; Ruff/format/Pyright/diff; real optional adapters; live provider install/execution deferred |
| T900-desktop-models | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe89b-5366-7100-9728-7cc6ca5c8444`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-desktop-models` at `/Users/sero/.codex/worktrees/0436/zana`, base `6812372` | typed desktop API/client/hooks plus Home/Models/Doctor views/styles/tests/handoff | active | frozen integrated backend API contract | complete real discovery/manual/refresh/pull/system UI vertical; focused unit/type/lint/format only; no app/browser/build/live runtime |
| T900-tauri-core-package | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe8a6-6bba-7791-bbfb-cbec9b48b0d0`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `codex/zana-t900-tauri-core-supervisor` at `/Users/sero/.codex/worktrees/edc1/zana`, base `c995466` | Tauri/core sidecar/package scripts and handoff | active | canonical M0 Tauri supervisor | robust lightweight sidecar packaging/lifecycle/auth/path errors; source/static checks only; no compile/bundle/app launch |
| T007-platform-wiring | `router_opencode_go_deepseek_v4_flash`, thread `019fe5f7-08a9-7972-baaa-95df844cb977`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-platform-wiring` at `/Users/sero/.codex/worktrees/9529/zana` | `core/zana_core/main.py`, one platform integration test, `T007-platform-wiring.md` | done, PASS; integrated `72dc76d`, `3e96e51` | integrated canonical platform boundary | 69 integrated platform/API pytest; Ruff/Pyright; explicit DB path bypass preserved; safe canonical production DB path |
| T007-runtime-hardening | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe5f7-08a9-7972-baaa-95df844cb977`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-runtime-hardening` at `/Users/sero/.codex/worktrees/9529/zana` | exact runtime registry/limits tests, `T007-runtime-hardening.md` | done, PASS; integrated `2f79a9d` | integrated runtime discovery + resource policy | 62 focused; 87 runtime; 987 full Core pytest; Ruff/format/Pyright; strict targets/workers/timeouts; cap+1 hostile iterable bound; sanitized failures; zero surviving threads |
| T007-runtime-discovery-integration | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe7f1-0e6f-7493-92e4-a2a9bc93957d`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-runtime-discovery-integration` at `/Users/sero/.codex/worktrees/7d3d/zana` | runtime registry/limits hardening, hostile projection/config tests, `T007-runtime-discovery-integration.md` | done, PASS; integrated `b862637`, receipt `f94f013`, pushed | canonical runtime/resource policy | 120 runtime tests; Ruff/format/Pyright; loopback/query/credential/config/model-identity gates; no live runtime/model/network |
| T007-knowledge-hardening | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe6d7-9e33-7a51-a3f1-918e95e65c1d`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-knowledge-hardening` at `/Users/sero/.codex/worktrees/9b02/zana`, base `8402887` | `core/zana_core/knowledge/**`, `core/tests/knowledge/**`, hardening plus canonical integration handoffs | done, PASS; integrated `8a64ee8`, receipt `a5867c1`, pushed | integrated knowledge + embeddings; strict lightweight owner constraint | 204 focused tests; Ruff/format/Pyright; hostile serialization/union probes; hard bounded knowledge pipeline; no model/network/live backend |

## Single-owner shared contracts

These files/directories must have one integrator and are not parallel-owned:
root manifests and lockfiles, `core/pyproject.toml`, migrations, backend API
boundary, generated TypeScript API types, build orchestrator, and this ledger.

## Integration receipts

### T900 Docling and LanceDB providers

- accepted integration head: `d84c22f9f3c86e8b461b4ae9f68ef3f734e06829`
- source commits: `3ecadeb`, `b6371ba`, `d7e9a56`, `ecc94c1`, `4bf42d3`, `36d2975`
- changed paths: bounded Docling parsing, persistent LanceDB index, parser error
  projection, snapshot/retrieval bridges, focused provider tests, and handoff
- gates: direct lead contract/security/error/readability inspection PASS; six
  exact Docling conversion-result and LanceDB query-builder tests PASS; Ruff
  check/format PASS; Pyright zero errors/warnings; `git diff --check` PASS
- security delta: approved exact-file integrity checks before conversion;
  bounded hostile iterables/materialization; immutable index identity; no table
  overwrite after failed upsert; sanitized optional-provider failures
- residual risk: Docling and LanceDB were not installed or run live under the
  host-safety policy; their real provider paths require later bounded live proof
- remote proof: non-force push succeeded; local HEAD and `origin/main` both
  resolved to `d84c22f9f3c86e8b461b4ae9f68ef3f734e06829` before this receipt update
- clean proof: canonical index/worktree and source agent worktree were clean

### T900 system/runtime/model API

- accepted integration head: `83428819d137879bce3cf4d8bff4128b631c5f0a`
- source commits: `9176532`, `e48e02c`, `48b89cb`, `797467e`
- changed paths: authenticated system profile/doctor, runtime refresh and model
  discovery persistence, native pull request API, exact runtime repository query,
  app-state wiring, focused tests, and the worker handoff
- gates: direct lead contract/security/error/readability inspection PASS; 96
  focused API/db/platform tests PASS; 1611 full Core tests PASS; Ruff check and
  format PASS; Pyright zero errors/warnings; `git diff --check` PASS
- security delta: loopback-authenticated bounded diagnostics and discovery;
  exact `(kind, endpoint, source)` runtime identity; online-only model pruning;
  savepoint rollback on refresh failure; explicit user approval and local-only
  endpoint validation for native pull plans; no byte proxy or secret persistence
- residual risk: the pull endpoint currently persists an acquisition plan; the
  real executor/progress UI remains a later vertical. No live runtime, model,
  network, download, inference, training, desktop, or bundle operation ran.
- remote proof: non-force `main` push succeeded; local HEAD and `origin/main`
  both resolved to `83428819d137879bce3cf4d8bff4128b631c5f0a`
  before this receipt-only update
- clean proof: canonical index/worktree and the source agent worktree were clean

### Agent delivery-cycle policy

- accepted commit: `390500c8ce6c9df4702363130c1cc8f6b257bd6e`
- changed paths: `AGENTS.md`, `docs/agent-adoption-map.md`,
  `docs/goals/zana-mvp/state.yaml`, `.agent-work/ledger.md`
- gates: GoalBuddy state validator PASS; `git diff --check` PASS; direct policy
  inspection PASS; canonical repo/HEAD/branch/remote/status/worktree preflight PASS
- security delta: no product/runtime surface; delivery policy now forbids Pro,
  force push, shared writer worktrees, false push claims, and advancing red gates
- residual risk: already-running T007 lanes began under the prior commit cycle;
  their next lead integration is subject to the hardened acceptance/push receipt
- remote proof: local `master` pushed non-force to `origin/main`; confirmed local
  HEAD and `origin/main` both equal the accepted commit SHA above
- clean proof: index exit `0`, worktree exit `0`, `git status --porcelain` empty

### T007 native model acquisition

- accepted commit: `8eaefc506055c551cb703b1fe0cce5c154015046`
- changed paths: `.agent-work/handoffs/T007-acquisition.md`,
  `core/zana_core/acquisition/**`, `core/tests/acquisition/**`
- gates: 83 focused pytest PASS; Ruff check PASS; Ruff format check PASS;
  Pyright PASS with zero errors/warnings; `git diff --check` PASS; lead security
  and error-path inspection PASS
- security delta: only native Ollama acquisition is admitted; progress parsing is
  bounded JSONL with one absolute deadline, resource admission, cancellation,
  sanitized failures, and no secret, raw-error, background-worker, or proxy path
- residual risk: transport remains injected and live Ollama/network execution was
  intentionally excluded; API/job wiring is a later bounded milestone
- remote proof: non-force push `master:main` succeeded; local HEAD and
  `origin/main` both equal `8eaefc506055c551cb703b1fe0cce5c154015046`
- clean proof: index exit `0`, worktree exit `0`, `git status --porcelain` empty

### T007 runtime probe hardening

- accepted commit: `2f79a9ddffc22291eda50ddf417d4c19d38691c7`
- agent delivery head: `5d8c96f2573097d75ee830e9192c2992aae29f1d`
- changed paths: `.agent-work/handoffs/T007-runtime-hardening.md`,
  `core/zana_core/runtimes/registry.py`, `core/zana_core/runtimes/limits.py`,
  `core/tests/runtimes/test_registry_hardening.py`,
  `core/tests/runtimes/test_limits.py`
- gates: 62 focused pytest PASS; 87 runtime pytest PASS; 987 full Core pytest
  PASS; Ruff check PASS; Ruff format check PASS; Pyright zero errors/warnings;
  `git diff --check` PASS; direct lead security/error/readability inspection PASS
- security delta: hard target/worker/timeout/string/model/output caps; strict public
  numeric types; one cap+1 bounded collection path that never trusts
  `Sequence.__len__`; scoped joined threads only; bounded validated descriptors;
  credential, bearer, hostile-error, and non-string evidence sanitization
- residual risk: adapter response parsing remains protected by the integrated
  1 MiB transport cap but was outside this registry-owned scope; no live runtime,
  service, model, or network probe was performed
- remote proof: non-force push `master:main` succeeded; local HEAD and
  `origin/main` both equal `2f79a9ddffc22291eda50ddf417d4c19d38691c7`
- clean proof: index exit `0`, worktree exit `0`, `git status --porcelain` empty
