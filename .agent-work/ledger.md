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
| T900-runtime-inference | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe870-59d1-74f2-98b2-13d3d0dd9561`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-runtime-inference` at `/Users/sero/.codex/worktrees/2aa0/zana` | runtime inference adapters plus exact instance identity/selection/lifecycle files/tests/handoff | done, PASS; integrated/pushed `6d43efe` | canonical runtime transport + instance inference protocol | lead 71 focused fixture tests; Ruff/format/Pyright/import/diff; no live model/server/network |
| T900-knowledge-providers | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe870-ce4c-7c71-8e2b-afe450a0a6f3`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-knowledge-providers` at `/Users/sero/.codex/worktrees/934f/zana` | exact Docling/LanceDB adapter files/tests/handoff | done, PASS; integrated/pushed `d84c22f` | canonical knowledge models/limits | lead 6 exact provider-contract tests; Ruff/format/Pyright/diff; real optional adapters; live provider install/execution deferred |
| T900-desktop-models | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe89b-5366-7100-9728-7cc6ca5c8444`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-desktop-models` at `/Users/sero/.codex/worktrees/0436/zana`, base `6812372` | typed desktop API/client/hooks plus Home/Models/Doctor views/styles/tests/handoff | done, PASS; integrated/pushed `a1c87f4` | frozen integrated backend API contract | lead 31 focused Vitest + ESLint/TypeScript/diff; cool model-first real-data UI; no lead build/app/browser/live runtime |
| T900-capability-authoring-api | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe8b6-af8f-76e1-b818-49a3dccebd47`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-capability-authoring-api` at `/Users/sero/.codex/worktrees/f2c9/zana`, base `48a016c` | complete capability draft/source/reopen/validation API vertical and focused tests/handoff | done, PASS; integrated/pushed `9103bd8` | canonical capability/source validator and existing authenticated route contract | worker 82 new focused + 243 owned-surface; lead 82 focused; Ruff/format/Pyright/import/diff; atomicity, containment, bounded preflight, compensation matrix and disclosure review PASS; no build/training/model/live execution |
| T900-mlx-training-execution | `router_opencode_go_deepseek_v4_flash` with `max`, final thread `019fe97c-ac3b-7e31-84a1-301d887977e5`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-mlx-final-recovery-2` at `/Users/sero/.codex/worktrees/6412/zana`, lineage base `87e10a8` | complete real MLX-LM invocation/staging/executor/adapter-verification vertical and focused tests/handoff | done, PASS; integrated/pushed `254bf10` | canonical training identities/datasets/resources/cancellation; official mlx_lm.lora CLI contract | lead 153 focused fake/unit tests; Ruff/format; source Pyright; import/diff; three-pass lifecycle/ownership review; external codex review denied by source-egress guard; no MLX import/process/model/training run |
| T900-tauri-core-package | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe8a6-6bba-7791-bbfb-cbec9b48b0d0`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-tauri-core-package` at `/Users/sero/.codex/worktrees/edc1/zana`, base `c995466` | Tauri/core sidecar/package scripts and handoff | done, PASS; integrated/pushed `1bad9e3` | canonical M0 Tauri supervisor | lead lifecycle/security review; cargo fmt, bash syntax, JSON, source invariants, diff PASS; no compile/bundle/PyInstaller/app/live Core |
| T900-model-acquisition-execution | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe8d9-c4a2-7bd3-ba23-c86baeb37ce8`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-model-acquisition-execution` at `/Users/sero/.codex/worktrees/4a22/zana`, base `51c08ad` | real native model-acquisition transport/supervisor, persisted jobs/progress/cancel/recovery, shared discovery service, model/job/runtime API wiring, focused tests/handoff | done, PASS; integrated/pushed `dbad578` | integrated runtime/model API, acquisition contracts, persistent job/SSE lifecycle | lead 93 critical tests before and after integration; worker up to 301 focused; Ruff/format/Pyright/import/diff; no live runtime/network/model/download |
| T900-build-evaluation-image-execution | `router_opencode_go_deepseek_v4_flash` with `max`, recovery thread `019feb86-bf40-7c42-a2c8-31f84c4994f3`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | source `agent/t900-build-recovery-flash-max` at `/Users/sero/.codex/worktrees/c326/zana`, product `adf4fc3`, receipt `887f2c3`; accepted serial lineage at `/Users/sero/.codex/worktrees/a0a8/zana` through `357df5c`; failed thread/worktree read-only | dependency-complete persisted build analysis/baseline/approval/candidate evaluation/gates/image finalization plus exact build/image API and focused tests/handoff | done, PASS; source and serial worktrees clean; canonical integration/push pending | integrated capability/runtime/knowledge/training/OCI/jobs/artifact/resource/API contracts | worker 217 focused; lead 6 exact boundary tests; Ruff/format/Pyright/import/diff and lifecycle/transaction/digest/cancel/redaction/no-promotion review PASS; no broad/live/model/training/app/build |
| T900-desktop-product-flows | `router_opencode_go_deepseek_v4_flash` with `max`, original thread `019feac2-2d73-78d2-b40e-de8625e99ad4`, replacement thread `019fed72-e132-7823-8d01-82392963c6f2`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | accepted original product lineage through `3e62b6e`; failed eb61 correction diff preserved read-only; accepted replacement lineage `f753e80`, `4b32d11` at `/Users/sero/.codex/worktrees/42a4/zana` | desktop product flows plus instance/chat/memory UI | done, PASS via clean replacement; canonical integration/push pending | integrated desktop shell/models and frozen Core contracts | lead reproduced 39 focused Vitest plus TypeScript/ESLint/diff after correcting paging/digest/stream/completion/provenance/eligibility/error defects; no app/browser/build/install/live |
| T900-desktop-instance-corrections | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fed72-e132-7823-8d01-82392963c6f2`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-desktop-instance-corrections` at `/Users/sero/.codex/worktrees/42a4/zana`, base `3e62b6e`, product `f753e80`, receipt `4b32d11` | exact desktop instance API/components, `InstancesChatView.tsx`, focused tests and handoff; every other view/Core/Tauri/manifests/locks excluded | done, PASS; clean/no push; canonical integration pending | accepted desktop and frozen instance/chat/memory/SSE contracts; eb61 diff read-only evidence | worker and lead 39/39 focused Vitest; TypeScript/ESLint/diff; direct pagination/provenance/cancellation/identity/error review PASS; no install/app/browser/native/build/live/broad |
| T900-desktop-jobs-acquisition | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fed72-e132-7823-8d01-82392963c6f2`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | same isolated `agent/t900-desktop-instance-corrections` worktree `/Users/sero/.codex/worktrees/42a4/zana`, clean base `4b32d11` | strict desktop global-job client/hooks, new jobs component, existing RuntimesModels acquisition wiring, focused tests/handoff; no other view/navigation/Core/Tauri/manifests/locks | BLOCK: Founder Rust-first supersession; idle with preserved local evidence `dc364c1`/`716d89b`, dirty BLOCK handoff only, no push; superseded by T920 | accepted global jobs snapshot/cancel/fetch-SSE plus accepted desktop models flow | pre-stop 33 focused Vitest plus TypeScript/ESLint/diff are evidence only; no result accepted after supersession; symlinks removed; no install/app/browser/native/live/broad |
| T900-runtime-native-tools | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019feac3-43a7-7bb0-95e7-8ff70256c72e`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-runtime-native-tools` at `/Users/sero/.codex/worktrees/4a35/zana`, base `23b9034` | exact runtime inference/Ollama/OpenAI-compatible files/tests and handoff | done, PASS; integrated `2b33316` | bounded inference + trusted calculator/tool contracts | worker + lead 108 focused fake transport tests; Ruff/format/Pyright/import/diff; official Ollama contract review; no live runtime/model/network |
| T900-instance-chat-memory-api | `router_opencode_go_deepseek_v4_flash` with `max`, recovery thread `019fec71-ba60-7f41-bebc-063edb9bb702`, serial thread `019febdb-a3d2-7630-86e0-580c7661a3f2`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | source `/Users/sero/.codex/worktrees/f480/zana`, product `4d35792e`, receipt `3f225181`; serial product `820a086`, receipt `71e077e`, accepted lineage through `357df5c`; preserved failed source read-only | instances/memory plus isolated API/service/schema files/tests/handoff; shared main/API/DB files excluded from source task | done, PASS; source and serial worktrees clean; no push; canonical integration pending | persisted image/instance/memory schema and runtime/retrieval/tool foundations | worker + lead 68 focused; serial 101 combined; Ruff/format/Pyright 0/0/import/diff; transaction/RAM/cancellation/redaction/tool-boundary review PASS; task venv/caches absent; no live model/runtime/API/app |
| T900-portability-product-api | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019feac4-2564-7b73-8a90-7f7b58f24005`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-portability-product-api` at `/Users/sero/.codex/worktrees/8037/zana`, base `23b9034`; canonical product tip `9ea7e13` | portability plus new isolated API/service/schema files/tests/handoff; images/shared main/API/DB excluded | done, PASS; integrated and non-force pushed through acceptance `623b265`; router registration queued for serial wiring | integrated OCI/archive/import/security and artifact registry | worker + lead 256 focused portability/API/image tests; Ruff/format/Pyright/diff; exact graph/TOCTOU/no-clobber/cleanup/path/base-identity review; no large/live/install/broad tests |
| T900-resources-observability-api | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019feaf8-3352-7793-a2e5-df581e834b07`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-resources-observability-api` at `/Users/sero/.codex/worktrees/eadd/zana`, base `52bc1be` | resources/observability packages, isolated new API/schema/service files, focused tests and handoff; shared main/API/DB/jobs/desktop excluded | done, PASS; integrated/pushed through acceptance `2f283cb` | integrated conservative governor/snapshots/leases and bounded redacted local sinks | worker + lead 247 focused tests; Ruff/format/Pyright/import/diff; bounded safe projections, pagination/truncation/redaction/telemetry-off; no live/broad/background/export |
| T900-global-jobs-product-api | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019feb66-e32a-7c51-846c-9a5c66070666`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-global-jobs-product-api` at `/Users/sero/.codex/worktrees/c1fc/zana`, base `2f283cb`; product `14aff6f`, correction `2293da6`, receipt `ccb1872` | jobs API plus isolated job query/schema files and focused tests/handoff; existing `jobs/services.py` and shared main/schema/DB/acquisition/build/instance/desktop excluded | worker COMPLETE; lead review PASS; clean source; canonical integration/push pending | persisted jobs/events, model-pull cancellation and authenticated bounded SSE | lead final 89 owned+SSE and earlier 132 focused adjacent PASS; Ruff/format/Pyright/diff/security/error-path review PASS; four unrelated failures identical on base; no live/broad/model/app |
| T900-product-router-lifespan-integration | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019febdb-a3d2-7630-86e0-580c7661a3f2`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-product-router-lifespan-integration` at `/Users/sero/.codex/worktrees/a0a8/zana`, base `ce806b5`; correction `af6fa2f`, receipt `d6e42e5` | serial accepted build/evaluation/image + global-jobs lineage and exact main lifecycle/router reconciliation | done, PASS; source clean; canonical integration/push pending | accepted build/jobs/portability/resources/observability contracts | 95 focused tests; Ruff/format/Pyright/import/diff and first-error/duplicate-service/factory review PASS; no live/broad/model/app |
| T900-production-adapter-boundaries | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019febdb-a3d2-7630-86e0-580c7661a3f2`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-production-adapter-boundaries` at `/Users/sero/.codex/worktrees/a0a8/zana`, base `d6e42e5`; product `65258ca`, receipt `da2c619` | production build training/materializer factories, exact orchestrator resolution, focused tests/handoff; RAG/instance/capability/desktop/DB/API excluded | done, PASS; clean/no push; canonical integration pending | accepted real MLX executor/training and Ollama materialization contracts | 11 focused + 106 integration/API + 81 build/evaluation PASS; Ruff/format/Pyright/import/diff and lead identity/transaction/readability review PASS; no live provider/model/training/app; RAG identity explicitly blocked |
| T900-capability-contract-hardening | `router_opencode_go_deepseek_v4_flash` with `max`, recovery thread `019fecec-b64d-72a3-a40a-0a3382974eda`, serial thread `019febdb-a3d2-7630-86e0-580c7661a3f2`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | source worktree `/Users/sero/.codex/worktrees/f77d/zana`, product `9ed66449`, receipt `3887c447`; serial worktree `/Users/sero/.codex/worktrees/a0a8/zana`, integrated `805f9f1`, correction `d2cbed7`, receipt `5f5cd5a`; prior failed 9ca0 history retained truthfully | capability API, one internal `capabilities/source_removal.py`, capability-only shared schemas/repository sections, focused tests/handoff; every other build/job/image/instance/desktop/Tauri/migration/manifest/lock path excluded | done, PASS; serially integrated; source/serial clean; no push | integrated capability authoring/validation plus path-redaction contract | worker 119 focused; lead 11 critical rollback/race/path; serial 73 capability + 120 combined; Ruff/format/Pyright/import/diff/security/transaction PASS; task venv/caches removed; no live/broad/model/app/native picker |
| T900-instance-runtime-integration | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019febdb-a3d2-7630-86e0-580c7661a3f2`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t900-production-adapter-boundaries` at `/Users/sero/.codex/worktrees/a0a8/zana`; source integration `820a086`, `71e077e`; product `a3b74d6`, corrections `b60eb6d`, `04fe30e`, receipt/head `357df5c` | exact instance/chat router registration plus lazy persisted lifecycle/inference bridges, default calculator-only tools, focused tests/handoff | done, PASS; serial worktree clean/cache-free; no push; canonical integration pending | accepted instance/chat/memory API and canonical runtime/provider adapters | worker 101 combined + lead 16 exact bridge tests; Ruff/format/Pyright/import/diff/identity/UoW/cancellation/security PASS; RAG fail-closed; no live model/runtime/provider/app/native work |
| T900-production-knowledge-rag-integration | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019febdb-a3d2-7630-86e0-580c7661a3f2`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | serial `agent/t900-production-adapter-boundaries` at `/Users/sero/.codex/worktrees/a0a8/zana`, clean base `357df5c` | explicit exact embedding selection/metadata plus lazy production build KnowledgeBuilder and instance retrieval reconstruction/wiring, focused tests/handoff; desktop/native/manifests/DB schema excluded | BLOCK: Founder Rust-first supersession; idle with uncommitted owned partial diff and BLOCK handoff preserved read-only, no push; superseded by T922 | accepted knowledge providers/bundle, build/image, artifact store, persisted runtime/model and instance chat contracts | 38 new/adjacent + 122 build/knowledge + 242 integration + 78 router/instance focused tests and static gates passed pre-stop; evidence only; exact invariants transfer to Rust |
| T920-rust-core-foundation | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019ff10d-934d-7b73-80eb-17cf14594203`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | source `agent/t920-rust-core-foundation` at `/Users/sero/.codex/worktrees/af77/zana`, base `ce806b5`; canonical accepted code through `ce34548`, handoff/head `31fbe1d` | root Rust workspace, `crates/zana-core/**`, `crates/zana-core-server/**`, minimal Tauri workspace boundary and focused Rust tests/handoff; Python/TypeScript/Android excluded | done, PASS; canonical integration complete; source/canonical code clean; push pending | authenticated loopback health/error, fail-closed platform data-root/SQLite bootstrap, bounded HTTP/concurrency/deadline and launch-token contracts; desktop cutover deferred to T925 after parity | 48 Rust tests; fmt/check/clippy `-D warnings`; shell/metadata/Tauri JSON/diff; four correction reviews then final Codex review clean; no app/bundle/model/provider/live/broad |
| T921-rust-operational-core | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019ff17c-9aad-7af1-8e9c-c330b19a4275`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | source `agent/t921-rust-operational-core` at `/Users/sero/.codex/worktrees/1668/zana`, clean base `8c11ac4`; canonical accepted operational stack through `64126f9` | exclusive shared Rust operational plane; compatibility server stayed T921B-exclusive; capability lifecycle, Python/TS/Tauri/Android/state/ledger excluded | done, PASS; serial canonical integration complete; source clean/no push; canonical push pending coordination receipt | accepted Python operational contracts plus T920 Rust foundation | lead fmt, 122 unit + 13 operational DB tests, workspace clippy `-D warnings`, diff/identity/shutdown/redaction review PASS; no live runtime/model/provider/network/download/app/device/broad |
| T921B-rust-compatibility-api | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019ff24f-7688-7153-926e-68c09175e299`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | reserved `agent/t921b-rust-compatibility-api`; isolated local worktree `/Users/sero/.codex/worktrees/0b16/zana` from canonical `bf46c8c`; must reconcile accepted core through `64126f9` before Phase B | only `crates/zana-core-server/**` plus exact new handoff; every shared core/other path excluded | Phase A preserved clean/idle; Phase B queued for next free writer slot; no push | accepted canonical DB/jobs/resources/doctor/runtime/settings/acquisition/observability/SSE modules | focused Rust server/router tests only; no external/live/model/runtime/provider/download/app/broad |
| T922A-rust-benchmark-engine | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019ff245-cb08-79c1-8514-43206fc16bb7`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | reserved `agent/t922a-rust-benchmark-engine`; isolated local worktree `/Users/sero/.codex/worktrees/4060/zana` | new standalone `crates/zana-evaluation/**` plus exact handoff only; every existing/shared path excluded | active visible implementation; no push | pinned T922 benchmark policy and T920 foundation only; later serial T922 integration | focused standalone Rust only; no dataset/model/network/provider/inference/training/app/device/root-workspace/broad |
| T922B-rust-artifact-domain | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019ff263-0b6e-7ad0-b123-debea7eb6440`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | reserved `agent/t922b-rust-artifact-domain`; isolated local worktree `/Users/sero/.codex/worktrees/0e5f/zana` from clean pushed `9aabfe6` | new standalone `crates/zana-artifacts/**` plus exact handoff only; every existing/shared path excluded | active visible implementation; no push | accepted platform-neutral capability/build/Image/Instance/portability contracts; later serial T922 integration | focused standalone Rust only; no archive extraction/model/dataset/network/provider/inference/training/app/device/root-workspace/broad |
| T922C-rust-knowledge-retrieval | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019ff274-cdc1-77b3-99e3-69a9f760eb0b`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | reserved `agent/t922c-rust-knowledge-retrieval`; isolated local worktree `/Users/sero/.codex/worktrees/0fd3/zana` from clean pushed `f03d650` | new standalone `crates/zana-knowledge/**` plus exact handoff only; every existing/shared path excluded | active visible implementation; no push | accepted extracted-document/chunking/provenance/embedding/retrieval contracts; later serial T922 integration | focused standalone Rust only; no filesystem/PDF/OCR/model/network/provider/training/app/device/root-workspace/broad |
| T922D-rust-build-planner | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019ff27e-cfac-74f3-9e47-d680131c74e9`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t922d-rust-build-planner` at isolated local worktree `/Users/sero/.codex/worktrees/88e1/zana` from clean pushed `1f30787` | new standalone `crates/zana-build/**` plus exact handoff only; every existing/shared path excluded | active visible implementation; no push | accepted capability analysis/build/training/resource contracts; later serial T922 integration | focused standalone Rust only; no provider/model/dataset/filesystem/network/process/training/app/device/root-workspace/broad |
| T922E-rust-permission-engine | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019ff27e-cfac-74f3-9e47-d6a175052b1e`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t922e-rust-permission-engine` at isolated local worktree `/Users/sero/.codex/worktrees/c6f9/zana` from clean pushed `1f30787` | new standalone `crates/zana-permissions/**` plus exact handoff only; every existing/shared path excluded | active visible implementation; no push | accepted permissions/tools plus Android safety contract; later serial T922/T923 integration | focused standalone Rust only; no OS action/accessibility/filesystem/network/process/model/provider/app/device/root-workspace/broad |
| T922F-rust-instance-memory | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019ff27e-cfac-74f3-9e47-d66fa715caac`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t922f-rust-instance-memory` at isolated local worktree `/Users/sero/.codex/worktrees/87bf/zana` from clean pushed `1f30787` | new standalone `crates/zana-memory/**` plus exact handoff only; every existing/shared path excluded | active visible implementation; no push | accepted mutable Instance conversation/memory/provenance/privacy contracts; later serial T922 integration | focused standalone Rust only; no persistence/filesystem/network/model/provider/inference/app/device/root-workspace/broad |
| T910-desktop-frontend-program | `gpt-5.6-sol` with `high`, thread `019feade-0a9a-7a63-a41a-5edcdc5c302a`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/t910-desktop-frontend-program` at `/Users/sero/.codex/worktrees/b5d3/zana`, base `a69d7c5`; source `f6d9ffa`; integrated `602e9ac` | new files only under `docs/desktop-frontend-program/**` plus `T910-desktop-frontend-program.md`; every existing file and all product code excluded | done, PASS; integrated and pushed | controlling spec, acceptance, canonical code/contracts/tests, current primary sources | SOL coverage/contract/citation/readability PASS; lead exact allowlist/package/security/relative-link/whitespace review PASS; 31/31 official URLs reported available; clean source/canonical trees; no product code/live model/heavy test/build/deploy |
| T007-platform-wiring | `router_opencode_go_deepseek_v4_flash`, thread `019fe5f7-08a9-7972-baaa-95df844cb977`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-platform-wiring` at `/Users/sero/.codex/worktrees/9529/zana` | `core/zana_core/main.py`, one platform integration test, `T007-platform-wiring.md` | done, PASS; integrated `72dc76d`, `3e96e51` | integrated canonical platform boundary | 69 integrated platform/API pytest; Ruff/Pyright; explicit DB path bypass preserved; safe canonical production DB path |
| T007-runtime-hardening | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe5f7-08a9-7972-baaa-95df844cb977`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-runtime-hardening` at `/Users/sero/.codex/worktrees/9529/zana` | exact runtime registry/limits tests, `T007-runtime-hardening.md` | done, PASS; integrated `2f79a9d` | integrated runtime discovery + resource policy | 62 focused; 87 runtime; 987 full Core pytest; Ruff/format/Pyright; strict targets/workers/timeouts; cap+1 hostile iterable bound; sanitized failures; zero surviving threads |
| T007-runtime-discovery-integration | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe7f1-0e6f-7493-92e4-a2a9bc93957d`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-runtime-discovery-integration` at `/Users/sero/.codex/worktrees/7d3d/zana` | runtime registry/limits hardening, hostile projection/config tests, `T007-runtime-discovery-integration.md` | done, PASS; integrated `b862637`, receipt `f94f013`, pushed | canonical runtime/resource policy | 120 runtime tests; Ruff/format/Pyright; loopback/query/credential/config/model-identity gates; no live runtime/model/network |
| T007-knowledge-hardening | `router_opencode_go_deepseek_v4_flash` with `max`, thread `019fe6d7-9e33-7a51-a3f1-918e95e65c1d`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-knowledge-hardening` at `/Users/sero/.codex/worktrees/9b02/zana`, base `8402887` | `core/zana_core/knowledge/**`, `core/tests/knowledge/**`, hardening plus canonical integration handoffs | done, PASS; integrated `8a64ee8`, receipt `a5867c1`, pushed | integrated knowledge + embeddings; strict lightweight owner constraint | 204 focused tests; Ruff/format/Pyright; hostile serialization/union probes; hard bounded knowledge pipeline; no model/network/live backend |

## Single-owner shared contracts

These files/directories must have one integrator and are not parallel-owned:
root manifests and lockfiles, `core/pyproject.toml`, migrations, backend API
boundary, generated TypeScript API types, build orchestrator, and this ledger.

## Integration receipts

### T900 Tauri Core supervisor and packaging

- accepted integration head: `1bad9e356b9d12e5e822c16c60ad31c8a454054a`
- source commits: `78d09d5`, `c6ac475`, `4a611b7`, `98319b7`
- changed paths: split Tauri Core supervisor/commands/errors/loopback/token
  modules, hardened Core packaging script, loopback-only offline dev script,
  and handoff
- gates: direct lead lifecycle/security/readability inspection PASS;
  `cargo fmt --check`, shell syntax, JSON sanity, source invariants, and
  `git diff --check` PASS
- security delta: one generation-checked child lifecycle; uncertain cleanup
  blocks replacement; fresh OS-CSPRNG bearer token; loopback-only Core;
  sanitized process errors; `--no-sync`; symlink containment and atomic
  sidecar publication
- residual risk: the shell plugin cannot pass a pre-bound socket, so a small
  bind/release/spawn race remains. Cargo compile/clippy/tests, PyInstaller,
  Tauri build/bundle/dev, app/browser, and live Core remain intentionally
  deferred under host safety
- remote proof: non-force push succeeded; local HEAD and `origin/main` both
  resolved to `1bad9e356b9d12e5e822c16c60ad31c8a454054a` before this receipt update
- clean proof: canonical index/worktree and source agent worktree were clean

### T900 Models and System desktop vertical

- accepted integration head: `a1c87f4115b54699220ca248e8fc28feb7fedef3`
- source commits: `278a1e9`, `0f698f7`, `3901d53`, `cd2e05c`, `5769a6e`
- changed paths: authenticated validated Core client/hooks, model-first Home and
  Runtime/Models surfaces, real Doctor/hardware view, focused tests, cool
  graphite/slate styling, and handoff
- gates: direct lead source/design/security/error review PASS; 31 focused
  Vitest tests PASS; ESLint PASS; TypeScript PASS; `git diff --check` PASS
- security delta: token is header-only and never rendered; raw transport
  exception text is suppressed; untrusted responses fail closed; destructive
  runtime deletion and pull queueing require explicit confirmation; Windows
  and POSIX host paths reduce to basename
- residual risk: the model pull endpoint remains honestly queue-only; no live
  Core/runtime/model/browser/app/native bundle proof ran under host safety. One
  bounded worker Vite build ran before the correction, was not repeated, and
  its generated worktree artifact was removed
- remote proof: non-force push succeeded; local HEAD and `origin/main` both
  resolved to `a1c87f4115b54699220ca248e8fc28feb7fedef3` before this receipt update
- clean proof: canonical and source worktrees were clean; worker-created
  temporary dependency symlinks and generated `dist` were removed

### T900 bounded runtime inference

- accepted integration head: `6d43efe70e69f8696e52178224bc5546b33d8a6f`
- source commits: `f1b34b4`, `8e2d87d`, `4791914`, `5de21f4`,
  `66382b3`, `29fea3b`, `0b94460`
- changed paths: bounded Ollama/OpenAI-compatible inference adapters and
  transport, runtime-native instance identity/selection/lifecycle checks,
  focused fixture tests, and handoff
- gates: direct lead contract/security/error/readability inspection PASS; 71
  focused inference/lifecycle fixture tests PASS; Ruff check/format PASS;
  Pyright zero errors/warnings; import smoke and `git diff --check` PASS
- security delta: bounded streaming and output; exact runtime/model/endpoint
  binding; fail-closed fragmented tool calls; strict tool identity/argument
  limits; sanitized transport and uncertain-cleanup failures
- residual risk: no live local runtime/model, provider network, browser, app,
  bundle, load, or performance verification ran under the host-safety policy
- remote proof: non-force push succeeded; local HEAD and `origin/main` both
  resolved to `6d43efe70e69f8696e52178224bc5546b33d8a6f` before this receipt update
- clean proof: canonical index/worktree and source agent worktree were clean

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

### T900 model acquisition execution

- accepted integration head: `dbad57831579d50c77448e825d1105fd8caa7831`
- source commits: `1ffce76`, `339f56f`, `2a87c1c`, `efe4b02`
- canonical commits: `137ee6a`, `fdaa5aa`, `9e11e64`, `dbad578`
- changed paths: native loopback pull transport, disk admission, persistent
  model-pull runner, lazy bounded supervisor, shared short-transaction runtime
  discovery, authenticated pull/cancel/runtime API wiring, app shutdown cleanup,
  and focused tests
- gates: direct multi-pass lead lifecycle/security/transaction inspection PASS;
  93 critical fake/unit tests PASS in the source worktree and again after
  canonical integration; worker owned-surface selectors up to 301 PASS; Ruff
  check/format PASS; Pyright zero errors/warnings; import smoke and diff hygiene
  PASS
- security delta: exact loopback `POST /api/pull`; fixed JSON body and headers;
  conservative secret-safe model references; explicit approval and ONLINE
  runtime identity; fail-closed disk/lease admission; bounded I/O, events,
  queues, targets, and persistence; cancellation-safe generation ownership;
  no DB transaction across injected network work; exact post-pull discovery
  confirmation; sanitized restart/shutdown/error behavior
- residual risk: no live Ollama/runtime/network/model download, API server,
  desktop/browser, bundle, broad suite, performance, or load verification ran;
  real urllib/Ollama interoperability and cancellation timing remain for a
  later bounded live session
- remote proof: non-force push succeeded and `git ls-remote` confirmed
  `refs/heads/main` exactly at `dbad57831579d50c77448e825d1105fd8caa7831`
  before this receipt-only update
- clean proof: canonical index/worktree and source agent worktree were clean;
  the writer's temporary out-of-ownership repository edit was removed before
  commit and no shared-contract change remains

### T900 runtime native tools

- accepted coordination head: `f0d75259d24d5a4fde8434e409ab9dd157303859`
- source product commits: `0083cb4`, `c79c78b`, `409e66c`; canonical product
  commits: `693e7a6`, `605970f`, `5f805f5`; canonical handoff/receipt head:
  `2b33316`
- changed paths: bounded Ollama/OpenAI-compatible inference request and stream
  adapters, focused inference/native-tool tests, worker handoff, and GoalBuddy
  acceptance state
- gates: worker and lead independently ran 108 focused fake-transport tests;
  Ruff check/format and Pyright passed; direct contract/security/failure-path
  review covered the official Ollama two-event stream, exact `tool_name`,
  request-local aliases, cumulative tool bounds, duplicate events, and zero
  tool requests on malformed, failed, partial, truncated, cancelled, timed-out,
  cleanup-failed, or overflow results
- security delta: native schemas cross the provider boundary only when supplied;
  provider-safe aliases map back to canonical tool identities; undeclared,
  duplicate, malformed, oversized, non-finite, or partial tool calls fail closed;
  runtime adapters transport/parse but never permit or execute tools
- residual risk: no live Ollama/OpenAI-compatible runtime, model, provider
  network, inference, app, browser, bundle, performance, or load proof ran under
  the current host-safety policy
- remote proof: non-force push succeeded and `git ls-remote` confirmed
  `refs/heads/main` exactly at `f0d75259d24d5a4fde8434e409ab9dd157303859`
  before this receipt-only update
- clean proof: canonical index/worktree and the source agent worktree were clean;
  no manifest, lockfile, API, DB, build, instance, permission, or tool-execution
  path was included

### T900 resources and observability product API

- accepted integration head: `295fc21`
- source commits: `0c31e3c`, `73bc215`, `4f04fc1`, `4c94c71`; canonical
  commits: `287a6c8`, `a685567`, `b48c694`, `295fc21`
- changed paths: bounded resource service/governor history, bounded local
  observability registry, strict authenticated resource/observability API
  projections, focused tests, and worker handoff
- gates: direct lead contract/security/failure/readability inspection PASS;
  247 focused resource/observability/API tests PASS in the isolated worktree
  and again on canonical main; Ruff check/format PASS; Pyright zero
  errors/warnings; `git diff --check` PASS
- security delta: no raw lease tokens, arbitrary request identifiers, host
  paths, secrets, or raw exceptions cross the API; event identifiers are
  sanitized before every sink; retention is bounded by count and exact UTF-8
  accounting bytes; telemetry and background polling remain disabled
- residual risk: routers, service construction, log-root setup and deterministic
  registry shutdown are intentionally deferred to the serial shared `main.py`
  integration scope; no live API/app/browser/model/runtime/provider, broad,
  performance or load proof ran under the host-safety policy
- remote proof: non-force push succeeded and `git ls-remote` confirmed
  `refs/heads/main` exactly at `f1cfebde49af0d2ccf949f08044e2450c7de52af`
  before this final receipt commit
- clean proof: canonical index/worktree and source agent worktree were clean

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
