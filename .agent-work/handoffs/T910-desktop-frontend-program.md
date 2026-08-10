# T910 Desktop Frontend Program Handoff

Verdict: PASS

## Scope and authority

Completed the Founder-authorized documentation/design-program task for the future production ZANA desktop frontend. No product code, existing documentation, GoalBuddy state, ledger, manifest, lock, schema, test, asset, or other handoff was modified.

Writer preflight:

- canonical product/code inspected at `a69d7c5425b3a91aba26cb25beb30a94180faed9`;
- lead-only activation commit `250bdc7dd46e15e7f5dfe42eeb5459eef3666e08` inspected separately and confirmed to change only GoalBuddy coordination;
- task branch/worktree: `agent/t910-desktop-frontend-program` at `/Users/sero/.codex/worktrees/b5d3/zana`, clean base `a69d7c5`;
- remote: `origin https://github.com/sero63211/zana.git`;
- exclusive ownership: new files only under `docs/desktop-frontend-program/**` plus this new handoff.

## New files

- `docs/desktop-frontend-program/README.md`
- `docs/desktop-frontend-program/product-blueprint.md`
- `docs/desktop-frontend-program/screen-flows-and-states.md`
- `docs/desktop-frontend-program/contract-ledger.md`
- `docs/desktop-frontend-program/delivery-verification.md`
- `docs/desktop-frontend-program/research-sources.md`
- `docs/desktop-frontend-program/kimi-k3-gold-prompt.md`
- `docs/desktop-frontend-program/goal-launcher.md`
- `.agent-work/handoffs/T910-desktop-frontend-program.md`

No existing file was edited.

## Delivered program

- Cold-readable product/UX/technical blueprint for the real Tauri/React/Core desktop operator application.
- Full two-level information architecture and complete screen flows/state machines for Dashboard, Models, System Doctor, Capabilities, Knowledge, Builds, Evaluations, ZANA Images, Instances/chat/memory, Jobs, Observability, Resources, Settings, and Export/Import.
- Cross-screen loading/empty/stale/validation/permission/unsupported/offline/streaming/cancel/retry/recovery/conflict/failure contracts.
- Exact current-vs-proposed ledger distinguishing registered canonical routes, internal-only tested foundations, partial behavior, active unaccepted work, and backend/API/native gaps.
- Integration rules for token/auth, runtime validation, header-bearing bounded SSE pages, query persistence boundaries, filesystem approval, SQLite/WAL, resources/leases, redaction/observability, Tauri capabilities/CSP, and packaging.
- Desktop design direction, component responsibility hierarchy, responsive/window behavior, keyboard/accessibility requirements, performance budgets, and Kimi-owned visual acceptance method.
- Dependency-ordered rollout, worker ownership model, focused/full/live gates, failure recovery, local kill switches, observability requirements, rollback, and exact completion criteria.
- English Kimi K3 Gold Prompt and inert copy-paste Founder `/goal` launcher.

The required labor model is explicit throughout: Kimi K3 at maximum reasoning owns 100% of design/UX/frontend and final visual acceptance; only after a current Founder invocation may Kimi use visible first-class `router_opencode_go_deepseek_v4_flash` workers at `max` for disjoint nonvisual lanes; Pro/silent substitution are forbidden; workers cannot stage/commit/push/deploy/run/download models/access provider state; lead/root integrates.

## Checks and evidence

- Read the required ZANA authority files, full controlling `00_READ_FIRST.md` routing graph (01-27 in order), current canonical frontend/Tauri/API/DB/domain/resource/observability contracts and relevant tests/handoffs.
- Current primary-source research used only official/first-party/standards-owner pages. Direct URL availability check: 31/31 returned HTTP 200 on 2026-08-10.
- Coverage review: all 14 required program destinations have explicit purpose, flow, state machine, and failure/recovery behavior; shared state matrix covers every requested state family.
- Contract review: accepted routes and gaps were checked directly against `core/zana_core/main.py`, API routers/schemas, domain enums, job services/state machines, Tauri config/commands, desktop client/views, migrations, and accepted handoffs.
- Critical conflict captured: canonical model acquisition now executes bounded pulls and post-pull discovery, but existing desktop copy still says queue-only.
- Citation review: all research claims are paraphrased/synthesized; no competitor expression copied; every citation is direct and has access date.
- Readability review: package has an explicit reading order, truth hierarchy, definitions, implementation boundary, cross-links, tables, and one reusable Gold Prompt; 8 program files total 1,372 lines / 15,428 words before this handoff.
- `git diff --check`: PASS.
- Allowlist review: `git status --short` showed only the new reserved documentation directory before this handoff; after this handoff it must show only the two authorized new-file roots. Index remains empty.
- No lint/build/test suite, browser/app/native run, model/provider/runtime operation, dependency install, or generated asset was needed or run.

## Principal contract gaps for the lead/future Kimi run

1. Freeze/integrate build-plan/approval/execution/evaluation/image and instance/chat/memory/portability product routes; active worker branches are not canonical truth.
2. Add global paginated Jobs with explicit cancellability/recovery/operation identity; implement the authenticated fetch-SSE page client and snapshot reconciliation.
3. Add least-privilege native file picker/approved-path and OS credential-reference boundaries; current Tauri has neither.
4. Remove/sanitize `CapabilityRead.working_dir` and formalize safe path projections, version handshake, optimistic concurrency, idempotency, freshness, pagination, and approval invalidation.
5. Add knowledge, resource/lease, observability, settings, image-runnability/detail/reference, and diagnostic-export APIs before enabling those screens.
6. Re-baseline after active T900 integration; do not consume worker branches or this snapshot as current truth.

## Security delta

Documentation only; no runtime attack surface changed. The program strengthens the future security contract: header-only ephemeral token, no token/path/secret/raw-error persistence or display, least Tauri authority, restrictive CSP, native approved paths with Core revalidation, default-deny network/tools, bounded streams/lists/logs, no remote telemetry, exact identity/digest gates, and safe destructive/import/export behavior.

## Residual risk

- This is a design/engineering program, not implementation or live/native proof.
- Canonical contracts will change as active T900 lanes integrate; the future Kimi run must re-baseline before coding.
- Several strong internal Core primitives remain unregistered/unwired, and current backend/provider evidence intentionally excludes many live integrations.
- Performance budgets are initial engineering targets and require measured validation/replacement on supported hosts.
- Current Tauri/native packaging lacks picker, credential, window-state, updater, signing/notarization, and full release evidence described as future prerequisites.

## Blockers

None for this documentation/design-program scope. The contract gaps above are explicit future dependencies, not reasons to misrepresent unavailable UI today.

## Lead integration instructions

1. Inspect only the nine new files listed above; verify no existing file changed.
2. Keep this package together; its internal reading order and cross-links are the integration unit.
3. Do not execute `goal-launcher.md` automatically. A current explicit Founder `/goal` command is required.
4. When execution is authorized, launch Kimi K3 at max reasoning as frontend/UX/design/final visual owner and follow the Gold Prompt. Use only visible Flash/max workers for permitted disjoint nonvisual lanes.
5. Re-baseline the contract ledger against the then-current canonical SHA before any writer and keep unresolved screens honest.

## Commit, index/worktree, and remote state

- SOL delivery commit created by the lead after independent acceptance: `f6d9ffa8aadc767896d161b36231be63eb06fd80`.
- Canonical integration commit: `602e9ac86cb2e5f65201b88dcfbeb1dd9d0697c5`.
- Independent lead gates: exact nine-file allowlist, direct package/contract/security/readability review, whitespace check, relative-link check, canonical model-acquisition conflict verification, staged diff check, and clean-tree checks PASS.
- Index/worktree proof: canonical `main` and the source worktree had empty index and tracked worktree after the focused commits; `git status --short` was empty.
- Push proof: non-force `git push origin main` succeeded and `git ls-remote origin refs/heads/main` returned `602e9ac86cb2e5f65201b88dcfbeb1dd9d0697c5` before this receipt-only update.
