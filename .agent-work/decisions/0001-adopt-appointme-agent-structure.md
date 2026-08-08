# Decision 0001 - Adopt Appoint Me Agent Structure, Not Product Rules

Date: 2026-08-09

Status: accepted

## Context

The controlling ZANA build plan requires adopting relevant Appoint Me
agent-management architecture before implementation. Appoint Me and ZANA are
different products, so only structural practices transfer.

## Decision

ZANA reuses Appoint Me's workspace preflight, concise root agent rules,
explicit task contracts, strict file ownership, isolated parallel work,
single-owner shared contracts, focused verification, PASS/BLOCK/ESCALATE
verdicts, compact handoffs, heartbeat accounting, and durable decision
records. ZANA does not adopt Appoint Me product/domain rules, the
`master`-only no-worktree override, stale model guidance, or unbounded
append-only logs.

GoalBuddy `docs/goals/zana-mvp/state.yaml` remains authoritative. Full mapping
lives in `docs/agent-adoption-map.md`.

## Affected contracts and owners

- Root policy: `AGENTS.md`
- Adoption map: `docs/agent-adoption-map.md`
- Coordination: `.agent-work/ledger.md`, handoffs, decisions, locks
- DeepSeek agents: `router_opencode_go_deepseek_v4_flash` and
  `router_opencode_go_deepseek_v4_pro` (exact installed router names)

## Supersedes

Nothing in ZANA. It resolves the conflict between Appoint Me direct-`master`
work and ZANA's isolated-worktree preference in favor of ZANA.
