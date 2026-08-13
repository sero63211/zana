# T927 Desktop UI Agent Prompt

Use this prompt only after the Founder explicitly authorizes the visible T927
task. The current Founder command on 2026-08-13 provides that authorization.

```text
You are visible first-class ZANA task T927-desktop-ui-execution.

Use exactly model opencode-go/deepseek-v4-flash with reasoning max. DeepSeek
V4 Pro and silent model substitution are forbidden. Lead task/session:
019fe396-2a2d-7493-ac98-a551ec7e4e1a. Quiet mode: report only BLOCK,
INTERFACE, and COMPLETE.

Read and obey AGENTS.md, docs/goals/zana-mvp/goal.md, authoritative
docs/goals/zana-mvp/state.yaml, docs/agent-adoption-map.md,
.agent-work/ledger.md, accepted desktop/Rust handoffs, and every file in
docs/desktop-frontend-program/. Read ui-execution-brief.md last and treat it as
the exact user-visible execution specification for this bounded task. Recheck
canonical code instead of trusting old implemented/proposed labels.

Before writing, prove your generated worktree is local and isolated; record
path, branch, exact base SHA, remote, clean status, and exclusive ownership.
You are not alone. Never overwrite, revert, delete, clean, stage, or stash
another task's work. Stop before an interface change.

Exclusive write ownership:
- apps/desktop/src/**
- .agent-work/handoffs/T927-desktop-ui-execution.md

Read-only:
- docs/desktop-frontend-program/**
- apps/desktop/package.json and all manifests/lockfiles
- every Core/Rust/Python/Tauri-native/Android path
- state.yaml, ledger, and other handoffs

Do not add a dependency or edit a manifest/lockfile. Do not write dist, cache,
generated screenshot, fixture-business-data, or temporary artifacts into Git.

Objective: implement one dependency-complete production desktop UI milestone,
not a mockup and not mini-slices. Build the six-destination ZANA shell, the
model-first Home, and the complete secondary screen architecture for Models,
Capabilities, Knowledge, Builds, Evaluations, Images, Instances, Jobs,
Resources, Observability, Doctor, Settings, and contextual Export/Import.
Preserve and improve every currently working Core-backed action. Wire only
accepted canonical contracts. For missing Rust-cutover contracts, render the
brief's concise truthful unavailable/recovery state; never fake data, progress,
benchmarks, compatibility, catalog entries, or success.

ZANA is a local capability workbench, not a chat app. Chat may appear only
inside a real selected Instance. Android is one real target/compatibility view,
not the whole product. The Home screen starts with actual discovered models.
Correct the stale queue-only acquisition copy after verifying the current
accepted native pull contract.

Implement ui-execution-brief.md exactly: calm graphite/slate/cobalt visual
system; no beige/orange/purple/neon/gradient/glow; no giant hero or serif
headline; no AI art; concise English copy; progressive disclosure; responsive
720x560 and 320 CSS px reflow; keyboard/focus/reduced-motion/forced-colors;
real loading/empty/error/offline/stale/unsupported/job/cancel/retry/recovery
states; no dead actions.

Keep the code lean. Add no UI framework, icon library, chart library, animation
library, font, global state store, or generic abstraction layer. Reuse the
existing inline SVG icons and primitives where they remain good. Use direct
imports, bounded lists, content-visibility for long rows, no background polling,
no redundant effect-derived state, and no request waterfalls. Lazy-load
secondary sections only where useful. Refactor monoliths into readable local
components without creating dozens of trivial files.

Verification is RAM-light and sequential:
1. focused Vitest files for changed shell/navigation/views/states;
2. existing desktop focused tests needed to prove no regression;
3. pnpm --filter @zana/desktop typecheck;
4. pnpm --filter @zana/desktop lint;
5. git diff --check;
6. direct review for contract truth, copy, accessibility, focus, bounded DOM,
   redaction, error/recovery behavior, dependency count, readability, and dead
   code.

Do not run Vite build, dev server, app, browser, Tauri/native build, live Core,
model/provider/runtime, download, training, device/emulator, broad E2E, load,
performance, network, install, or container commands. If an existing focused
test requires a prohibited live action, record the exact deferred proof.

Before completion, read the full diff, remove AI-slop copy, duplicate styles,
unnecessary wrappers, speculative abstractions, unused code, and avoidable
re-renders. A red gate must be repaired before committing.

Create exactly one focused accepted product commit, then a handoff receipt
commit. Do not push. The handoff must use PASS/BLOCK/ESCALATE and list exact
commits, changed paths, checks/counts, design/copy evidence, security delta,
performance/dependency delta, residual risks, deferred visual/native proof,
merge instructions, and clean index/worktree proof. Finish with one concise
COMPLETE or BLOCK message so the lead can continue immediately.
```
