# ZANA Agent Instructions

## Authority

GoalBuddy board truth lives in `docs/goals/zana-mvp/state.yaml`. This file and
`.agent-work/` are operational coordination and never supersede it.

## Read order

Before planning or coding, read:

1. `docs/goals/zana-mvp/goal.md` and `docs/goals/zana-mvp/state.yaml`
2. `docs/agent-adoption-map.md`
3. `.agent-work/ledger.md`
4. The relevant task brief in `docs/goals/zana-mvp/state.yaml`
5. Existing handoffs under `.agent-work/handoffs/`

## First-class agents

All ZANA implementation delegation MUST run as a visible first-class Codex
task and use only this agent:

- `router_opencode_go_deepseek_v4_flash` -> model
  `opencode-go/deepseek-v4-flash` with reasoning effort `max`

`router_opencode_go_deepseek_v4_pro` may be installed, but MUST NOT be used for
ZANA. There is no separate DeepSeek V4 Max profile or model: `max` is the
required reasoning effort for the Flash agent.

## Coordination rules

- Before every writer scope, verify the canonical repo, HEAD, branch, remote,
  clean status, worktree, and exclusive owner. Never place two writers in one
  worktree.
- Disjoint write ownership: one agent owns a file or directory at a time.
- Shared contracts have a single owner/integrator until they are stable.
- Preferred isolation: `agent/<task-id>-<short-name>` worktree/branch per task.
- Read the whole repo; write only owned paths.
- Stop and escalate before changing an interface contract.
- Never overwrite, revert, or delete another agent's work.
- Quiet notifications: report only blockers, interface changes, and completion.
- Keep `state.yaml` as the single authority; never copy Appoint Me product rules.

## Task contract

Each task defines:

- task id, agent, branch/worktree
- owned paths and dependencies
- input and output contracts
- state in `.agent-work/ledger.md`
- verification commands and evidence

A task is not complete until its verification passes and its handoff exists.

## Milestone acceptance cycle

Every implementation scope follows this order:

1. bounded dependency-complete scope with explicit gates and stop conditions
2. implementation, focused tests, and direct inspection/review
3. readability refactor plus security, failure-path, and relevant integration gates
4. exactly one focused accepted commit, followed by clean index/worktree proof
5. safe fetch/reconcile and non-force push under ZANA's remote policy
6. durable receipt, then the next writer scope

Do not accept, commit, push, or begin the next scope while a known gate is red.
Repair it first. Do not accumulate multiple accepted features in a dirty tree.
Never claim a push without the confirmed remote SHA. If push is unauthorized or
blocked, keep the focused local commit, record the blocker honestly, and do not
claim remote completion.

## Handoff contract

Handoffs live in `.agent-work/handoffs/<task-id>.md` and must include:

- verdict: PASS, BLOCK, or ESCALATE
- changed files and touched modules
- checks run and evidence
- security delta
- residual risk
- blockers
- merge instructions
- accepted commit SHA and clean index/worktree proof
- remote, push SHA/remote state, or an explicit push blocker

Use PASS | BLOCK | ESCALATE only. Gate agents block and hand back; they do not
silently feature-build. Escalate to a human when the controlling spec would be
intentionally superseded.

## Verification

Run the smallest meaningful check first, then relevant gates. Full suites and
live smoke tests require a touched-surface or risk justification. If broader
proof is skipped, say exactly what was run and what remains unverified.
