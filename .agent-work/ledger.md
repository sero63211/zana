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
| T004 | Worker | queued scope (see state.yaml) | core and apps/desktop files per state.yaml | queued | T003 handoff, `04_SYSTEM_ARCHITECTURE.md`, `05_TECH_STACK.md`, `18_BACKEND_API_CONTRACT.md`, `21_REPOSITORY_STRUCTURE.md` | `uv` pytest/ruff/pyright, pnpm lint/typecheck/test, cargo fmt/clippy, auth health smoke |

## Single-owner shared contracts

These files/directories must have one integrator and are not parallel-owned:
root manifests and lockfiles, `core/pyproject.toml`, migrations, backend API
boundary, generated TypeScript API types, build orchestrator, and this ledger.
