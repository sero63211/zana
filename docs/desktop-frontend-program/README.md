# ZANA Desktop Frontend Program

Status: execution-ready design and engineering program; product implementation has not been authorized by this package.

Snapshot basis: canonical product/code truth inspected at `a69d7c5425b3a91aba26cb25beb30a94180faed9`; the lead-only coordination activation at `250bdc7dd46e15e7f5dfe42eeb5459eef3666e08` changes no product contract. Accessed/reviewed 2026-08-10.

## Purpose

This package defines the future production desktop application for ZANA: a local-first operator workspace for discovering runtimes and Base Models, diagnosing the host, authoring Capability Sources, building and evaluating candidates, managing immutable ZANA Images and mutable ZANA Instances, inspecting knowledge and jobs, observing resource and security state, and safely exporting or importing OCI artifacts.

It is not a marketing site, generic dashboard, mockup shell, or permission to implement. It does not treat an active worker branch, a proposed endpoint, a test fixture, or a specification example as production truth.

## Reading order

1. [`product-blueprint.md`](product-blueprint.md) — product boundary, information architecture, desktop behavior, shared state model, accessibility, security, resource safety, and component architecture.
2. [`screen-flows-and-states.md`](screen-flows-and-states.md) — every required screen, flow, state machine, failure, cancellation, retry, and recovery state.
3. [`contract-ledger.md`](contract-ledger.md) — canonical implemented truth, proposed contracts, conflicts, and backend/API gaps that must be resolved before UI wiring.
4. [`delivery-verification.md`](delivery-verification.md) — Kimi-led labor model, dependency order, rollout, gates, kill switches, observability, recovery, visual acceptance, and completion proof.
5. [`research-sources.md`](research-sources.md) — current primary/official sources, direct URLs, synthesis, and access date.
6. [`kimi-k3-gold-prompt.md`](kimi-k3-gold-prompt.md) — the English implementation prompt for the future Design Director and frontend owner.
7. [`goal-launcher.md`](goal-launcher.md) — the short copy-paste Founder launcher. Merely storing it starts nothing.
8. [`ui-execution-brief.md`](ui-execution-brief.md) — the current exact, minimal, no-slop desktop UI direction authorized for T927.
9. [`ui-agent-prompt.md`](ui-agent-prompt.md) — the cold-readable visible Flash-Max implementation task prompt.
10. [`ui-goal-launcher.md`](ui-goal-launcher.md) — the short Founder copy-paste command for starting the UI task manually.

## Truth hierarchy

At implementation time, use this order and stop on conflict:

1. The then-current explicit Founder command and repository `AGENTS.md`.
2. `docs/goals/zana-mvp/state.yaml` for board and ownership truth.
3. The controlling ZANA specification graph rooted at `/Users/sero/Downloads/ZANA_BUILD_PLAN_DETAILED/00_READ_FIRST.md`.
4. Accepted canonical code, migrations, tests, and handoffs on the integration branch.
5. This package for the frontend program.
6. Research references as design/technical guidance, never as ZANA product contracts.

Do not average conflicting sources. If the specification expects behavior that canonical Core does not expose, the UI must show an honest unavailable/blocked state until the contract owner supplies and verifies the missing contract.

## Non-negotiable product invariants

- Runtime is not a Base Model; Capability Source is editable; ZANA Image is immutable; ZANA Instance is mutable.
- Real Core state is the only source for records, counts, metrics, progress, verification, citations, compatibility, resource headroom, and success.
- Unknown metadata stays unknown. A display name never substitutes for an exact model digest.
- Baseline precedes specialization. Failed gates never produce a verified image or replace the last verified image.
- Model acquisition, training, destructive operations, external network access, filesystem access, permissions, and export/import require explicit operator understanding and the exact backend approval contract.
- No token, credential, raw traceback, private document body, unrestricted host path, or secret value is rendered, logged, placed in a URL, persisted by the frontend, or exported.
- Long work is durable Core work. Closing, refreshing, or navigating the UI cannot become the authoritative cancellation mechanism.
- Cancellation is an acknowledged state transition, not a button animation. `Cancel requested` remains distinct from `Cancelled`.
- Offline is a supported mode. Missing dependencies and unsupported hardware are reported, never papered over with downloads or guessed compatibility.
- Every action is keyboard reachable, status is not color-only, focus is visible, reduced motion is honored, and dialogs restore focus.
- No dead buttons. An unavailable action is either absent or disabled with a nearby, testable reason and recovery route.

## Labor model encoded by this package

- Kimi K3 at maximum reasoning is Design Director, UX authority, desktop frontend architecture/code owner, and final visual acceptance owner.
- Kimi owns 100% of design, navigation, interaction, window behavior, component design, UI states, and frontend decisions.
- Only after a separate current explicit Founder execution command may Kimi orchestrate visible first-class `opencode-go/deepseek-v4-flash` workers at reasoning `max`, and only for mutually disjoint backend/API/database/runtime/security/job-event/packaging/shared-contract/non-visual-test lanes.
- DeepSeek V4 Pro, silent model substitution, and a fictional separate "DeepSeek Max" model are forbidden. `max` is the required reasoning effort for Flash.
- DeepSeek does not invent design or UX. Kimi specifies every user-visible behavior and contract expectation, monitors workers, reviews their work, and proves end-to-end integration.
- Worker agents do not stage, commit, push, deploy, run or download local models, or access production/provider state. The ZANA lead/root integrates accepted scopes under repository rules.
- Nothing starts because these files or the Gold Prompt exist. A current explicit Founder `/goal` invocation is required.

## Package completion boundary

This package is complete when it is cold-readable, internally consistent, source-backed, and explicit about missing contracts. It does not claim the desktop product is implemented or accepted. Product completion still requires the future implementation, contract integration, native packaging, real local runtime/model evidence, accessibility review, and every mandatory item in `25_ACCEPTANCE_CRITERIA.md`.
