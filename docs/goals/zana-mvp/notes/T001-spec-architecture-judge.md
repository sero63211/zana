# T001 — Specification and Agent Architecture Judge

Verdict: PASS for discovery and adoption decisions; BLOCK for feature fan-out before M0.

## Path corrections

- Controlling specification: `/Users/sero/Downloads/ZANA_BUILD_PLAN_DETAILED/00_READ_FIRST.md`.
- Appoint Me agent architecture: `/Users/sero/Documents/appointMe`.
- The originally supplied `/Users/sero/Downloads/ZANA_BUILD_PLAN` and `/Users/sero/Documents/Appoint Me` paths do not exist.

## Evidence read

- `00_READ_FIRST.md` through `28_PRIOR_AUDIT_FINDINGS.md` in the corrected build-plan directory.
- `FILE_INDEX.md`, `GOAL_PROMPT.md`, `schemas/README.md`, and every file present under both example capabilities.
- Appoint Me `AGENTS.md`, agent instructions, handoff protocol, done-means-done, test/evidence rules, task contract, subagent playbook, memory/handoff/eval sources, and relevant architecture routing.
- Current ZANA workspace, local toolchains, runtime ports, Python packages, Codex Router profiles, and host capacity.

## Adoption decisions

Adopt for ZANA:

- canonical-workspace and dirty-state preflight;
- root `AGENTS.md` as concise policy/router with detailed scoped docs;
- explicit task scope, non-goals, acceptance, proof, and stop conditions;
- strict file ownership, single ownership for shared contracts, focused verification first;
- `PASS | BLOCK | ESCALATE` gate verdicts;
- compact durable handoffs with changed files, tests, security delta, residual risk, blockers, and merge instructions;
- heartbeat accounting for every parallel lane with quiet user-facing status;
- extend durable knowledge and explicitly supersede conflicts.

Do not adopt:

- Appoint Me product, salon, PostgreSQL, tenant, Keycloak, payment, or mobile UI rules;
- Appoint Me's repo-specific `master`-only/no-worktree override, because ZANA explicitly prefers isolated worktrees;
- stale model guidance or unbounded append-only handoff/memory logs.

GoalBuddy `state.yaml` remains board truth. `.agent-work/` is operational coordination and never supersedes it.

## DeepSeek profiles

- `router_opencode_go_deepseek_v4_flash` -> `opencode-go/deepseek-v4-flash`.
- `router_opencode_go_deepseek_v4_pro` -> `opencode-go/deepseek-v4-pro`.
- Both are visible first-class Codex agents and support `max` reasoning.
- There is no separate installed DeepSeek V4 Max model/profile; `max` is a reasoning effort and must not be presented as another agent.

## Repository and host facts

- The workspace is pre-M0 and is not yet a Git repository.
- It contains only the GoalBuddy control files.
- macOS ARM64, Apple M2 Pro, 16 GB RAM; Python 3.12, uv, Node/pnpm, Rust/Cargo, Xcode are present.
- No Ollama, LM Studio, llama.cpp, MLX-LM server, local model, or embedding provider is currently available.
- Python ML dependencies `mlx`, `mlx_lm`, `lancedb`, and `docling` are absent.
- Approximately 6.5 GiB free disk space is the main acquisition/training risk; every dependency/model acquisition needs a measured safety preflight.

## Specification resolutions

- Dependency-injected runtime doubles are allowed only in automated tests. No UI/demo/acceptance claim may depend on fake runtime data.
- Build analysis performs analysis plus real baseline and ends in `PLANNED`; approval freezes the plan and begins candidate construction.
- Failed candidates remain inspectable through immutable BuildJob artifacts but never become verified images.
- Capability directory import needs a typed backend contract; it must not be faked in the client.
- Unsigned local ARM64 bundle is sufficient for MVP acceptance; notarization is a later release concern.
- Add the missing synthetic Information Handling policy demo and deterministic disjoint math train/validation/evaluation datasets.
- Never infer a trainable MLX checkpoint from an Ollama display name.
- Every external adapter must verify current primary documentation before implementation.

## Ordering and ownership

1. Create the mandatory `docs/agent-adoption-map.md`, root agent rules, coordination ledger, handoff format, and Git foundation.
2. Establish authenticated FastAPI Core and the Tauri/React health surface.
3. Settle domain protocols, schemas, DB, job, API, and frontend client contracts under single owners.
4. Only then fan out feature work into disjoint worktrees/scopes.

Shared files requiring a single integrator include root manifests/lockfiles, `core/pyproject.toml`, migrations, backend API boundary, generated TypeScript API types, build orchestrator, and `.agent-work/ledger.md`.

## Completion decision

- T001 complete: true.
- Full outcome complete: false.
- Next allowed task: T002 board correction and serial M0 activation.
