# ZANA Agent Adoption Map

Status: PASS

This map records which Appoint Me agent-management practices ZANA reuses,
which are irrelevant, and where the ZANA specification conflicts with Appoint
Me. It adapts structure only; Appoint Me product, salon, PostgreSQL, tenant,
Keycloak, payment, and mobile UI rules are not adopted.

GoalBuddy `docs/goals/zana-mvp/state.yaml` remains the authoritative board.
Nothing in this map or `.agent-work/` supersedes it.

## Adopted practices

| Practice | ZANA use |
| --- | --- |
| Canonical workspace and dirty-state preflight | Verify real local path, Git root, branch/worktree, and `git status --short` before material work; never use mirrors or archived copies as active source. |
| Root `AGENTS.md` as concise policy/router | Root rules stay hard policy, routing, and repo map; detailed operational detail lives in scoped docs and `.agent-work/`. |
| Explicit task scope | Every task defines scope, non-goals, acceptance, proof, and stop conditions. |
| Strict file ownership | One agent writes a file/directory at a time; shared contracts have a single owner/integrator. |
| Isolated parallel work | Separate `agent/<task-id>-<short-name>` worktree/branch per implementation task; lead owns integration. |
| Focused verification first | Run the smallest meaningful check first, then relevant gates; record exact evidence. |
| PASS/BLOCK/ESCALATE verdicts | Verdicts use only PASS, BLOCK, or ESCALATE. |
| Compact durable handoffs | Handoffs record changed files, checks, security delta, residual risk, blockers, and merge instructions. |
| Heartbeat accounting with quiet status | Lanes report only blockers, interface changes, and completion; no repetitive progress chatter. |
| Extend durable knowledge | Add decisions and explicitly supersede conflicts rather than replacing knowledge aggressively. |
| Security and contract review gates | API contract, security, test-gap, and UX consistency review agents run after implementation slices. |
| Accepted milestone receipts | A scope advances only after focused implementation/tests, inspection, readability refactor, security/error-path and relevant integration gates, one focused commit, clean proof, safe non-force push, and a receipt with local/remote SHAs. |

## Not adopted

| Appoint Me rule | Reason |
| --- | --- |
| Product/domain rules | ZANA has a different product; only agent-management structure transfers. |
| `master`-only, no-worktree override | ZANA explicitly prefers isolated worktrees per parallel task. |
| Stale model guidance | DeepSeek profile names must come from the installed Codex Router catalog, not older docs. |
| Unbounded append-only handoff/memory logs | ZANA keeps bounded coordination records under `.agent-work/`. |

## Conflicts with the ZANA specification

| Conflict | Resolution |
| --- | --- |
| Appoint Me canonical branch (`master`, direct work) vs ZANA isolated worktrees | ZANA wins: use worktrees/branches per parallel task and a single integration lane. |
| Appoint Me model guidance vs installed router profiles | ZANA's owner policy wins: implementation uses only `router_opencode_go_deepseek_v4_flash` with reasoning effort `max`; the installed Pro profile is prohibited for ZANA. |
| Appoint Me append-only handoff logs vs bounded coordination | ZANA uses a bounded ledger plus one handoff per task. |

## DeepSeek profile mapping

Installed first-class Codex Router agents:

| Agent name | Router model |
| --- | --- |
| `router_opencode_go_deepseek_v4_flash` | `opencode-go/deepseek-v4-flash`, required reasoning effort `max` |
| `router_opencode_go_deepseek_v4_pro` | Installed but prohibited for ZANA |

There is no separate installed DeepSeek V4 Max profile. `max` is a reasoning
effort and must not be presented as another agent.

## Interface-first sequencing

1. Settle domain contracts, adapter protocols, and DB schema under single owners.
2. Merge contracts before fanning out parallel feature work.
3. Merge gates: rebased branch, no ownership conflict, unit tests pass, type
   checks pass, handoff exists.
4. After a merge batch, run the full backend suite, frontend tests, and smoke
   build.

Stop and escalate before changing an interface contract. A gate agent blocks
and hands back; it does not silently feature-build.

## Accepted milestone cycle

`bounded scope -> build -> focused tests -> inspect/review -> readability
refactor -> security/error gates -> focused commit -> clean proof -> safe
fetch/reconcile and non-force push -> receipt -> next scope`

A missing or unauthorized remote blocks only the push claim: retain the
focused local commit, record the exact blocker, and never invent a remote SHA.
