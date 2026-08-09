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
| T005-contracts | `router_opencode_go_deepseek_v4_pro` | `agent/T005-contracts` at `/private/tmp/zana-worktrees/T005-contracts` | Core pyproject + Alembic config/migrations, `core/zana_core/domain/**`, `db/**`, `jobs/**`, backend API/schema integration, focused tests, `T005.md` | active | T004 PASS; specs 03/08/09/13/18/19 | typed entities/enums; SQLite WAL/FK/Alembic migrations; repositories; real job/event persistence; contract tests |
| T006-ui | visible Codex task `opencode-go/deepseek-v4-flash` | `agent/T006-ui`, thread `019fe603-86a7-7ee2-a8b3-15981062570e` | `apps/desktop/src/**`, `T006-ui.md` | done | frozen React/Tauri architecture; T004 health contract | commit `c957389`; `git diff --check`; heavy gates deferred for host safety |
| T006-docs | visible Codex task `gpt-5.6-terra` | isolated Codex worktree, thread `019fe60d-b3d2-79b0-b44a-152cbdb2fb03` | `README.md`, `docs/product/**`, `T006-docs.md` | done | T006-ui receipt and current implementation evidence | commits `4b101d0`, `3e672ac`; `git diff --check` |

## Single-owner shared contracts

These files/directories must have one integrator and are not parallel-owned:
root manifests and lockfiles, `core/pyproject.toml`, migrations, backend API
boundary, generated TypeScript API types, build orchestrator, and this ledger.
