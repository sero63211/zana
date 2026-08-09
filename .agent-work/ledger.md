# ZANA Coordination Ledger

`docs/goals/zana-mvp/state.yaml` is authoritative board truth. This ledger is
operational coordination only and never supersedes it.

## Conventions

- One row per task; update state as it changes.
- Shared contracts have a single owner/integrator; that owner is recorded in
  the row for the owning task.
- Stop and escalate before changing an interface contract; do not edit another
  agent's owned paths.
- Keep notifications quiet: blockers, interface changes, and completion only.
- Verification column records the exact commands or evidence required for
  `done`.
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
| T007-knowledge | `router_opencode_go_deepseek_v4_flash`, thread `019fe603-86a7-7ee2-a8b3-15981062570e`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-knowledge` at `/Users/sero/.codex/worktrees/216c/zana` | `core/zana_core/knowledge/**`, `core/tests/knowledge/**`, `T007-knowledge.md` | active wave | T007 runtime/artifact PASS; spec 10 | focused pytest/Ruff/Pyright; no embeddings/models started |
| T007-evaluation | `router_opencode_go_deepseek_v4_flash`, thread `019fe65b-1ebf-7132-b240-e6b98772bb56`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-evaluation` at `/Users/sero/.codex/worktrees/ba7c/zana` | `core/zana_core/evaluation/**`, `core/tests/evaluation/**`, `T007-evaluation.md` | done, PASS; integrated `48e4081`, `b70c94c` | T005 PASS; spec 12 | 39 focused pytest; 0 Ruff/Pyright findings; deterministic scorers/gates |
| T007-images | `router_opencode_go_deepseek_v4_flash`, thread `019fe65a-7992-7081-9d8a-3bef3f41e58b`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-images` at `/Users/sero/.codex/worktrees/b8bd/zana` | `core/zana_core/images/**`, `core/tests/images/**`, image schema, `T007-images.md` | active wave | artifact/permission PASS; specs 08/17 | focused pytest/Ruff/Pyright; OCI round-trip/corruption/traversal |
| T007-memory | `router_opencode_go_deepseek_v4_flash`, thread `019fe60d-b3d2-79b0-b44a-152cbdb2fb03`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-memory` at `/Users/sero/.codex/worktrees/d795/zana` | `core/zana_core/memory/**`, `core/tests/memory/**`, `T007-memory.md` | active wave | T005 PASS; spec 15 | focused pytest/Ruff/Pyright; explicit reset/update/rollback |
| T007-planning | `router_opencode_go_deepseek_v4_flash`, thread `019fe5f7-08a9-7972-baaa-95df844cb977`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-planning` at `/Users/sero/.codex/worktrees/9529/zana` | `core/zana_core/planning/**`, `core/tests/planning/**`, `T007-planning.md` | active wave | capability/hardware/runtime PASS; specs 07/11/13 | focused pytest/Ruff/Pyright; pure deterministic planner; no training/runtime execution |
| T007-training | `router_opencode_go_deepseek_v4_flash`, thread `019fe65b-1ebf-7132-b240-e6b98772bb56`, lead `019fe396-2a2d-7493-ac98-a551ec7e4e1a`, silent | `agent/T007-training` at `/Users/sero/.codex/worktrees/ba7c/zana` | `core/zana_core/training/**`, `core/tests/training/**`, `T007-training.md` | active wave | capability/evaluation/hardware PASS; spec 11 | focused pytest/Ruff/Pyright; metadata-only provider probes; no training/model load |

## Single-owner shared contracts

These files/directories must have one integrator and are not parallel-owned:
root manifests and lockfiles, `core/pyproject.toml`, migrations, backend API
boundary, generated TypeScript API types, build orchestrator, and this ledger.
