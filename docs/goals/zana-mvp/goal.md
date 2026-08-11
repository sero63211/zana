# ZANA Rust-First MVP and Android Agent Build

## Objective

Build and verify the complete real ZANA MVP from the controlling build-plan documents, then replace the Python product runtime with a Rust-first Core and add Android as a first-class local-agent execution target without losing the platform-neutral capability, knowledge, build, evaluation, Image, Instance, or portability lifecycle.

## Original Request

Execute `/Users/sero/Downloads/ZANA_BUILD_PLAN_DETAILED/00_READ_FIRST.md` (the corrected existing path), recursively follow every referenced ZANA file, adopt agent-management conventions from `/Users/sero/Documents/appointMe` (the corrected existing path), then build the functional desktop UI, backend, real local-model discovery, capability build/evaluation, ZANA Image/instance, and export/import flow until every mandatory criterion in `25_ACCEPTANCE_CRITERIA.md` passes.

On 2026-08-11 the Founder explicitly superseded the Python target architecture: the shipped MVP must use a Rust product Core, Android must be an explicit deployment/runtime target for a local device agent, and the existing medical, language-learning, knowledge/RAG, tool, and other platform-neutral use cases must remain supported. Python may survive only as an isolated optional development/provider worker where a required third-party ecosystem has no production Rust replacement; it must not remain the shipped authoritative Core.

## Intake Summary

- Input shape: `existing_plan`
- Audience: ZANA desktop users and the owner/operator
- Authority: `approved`
- Proof type: `test`
- Completion proof: every mandatory acceptance criterion passes against real local behavior; the authoritative product Core and shipped desktop control plane no longer require Python; Android can safely import and run a compatible ZANA Image through a local model/runtime and permission-gated action plane; medical, Kurdish/language-learning, and other knowledge capabilities remain platform-neutral; and a final Judge audit records `full_outcome_complete: true`
- Likely misfire: producing a polished mockup, partial scaffold, fake-data demo, or plan-only outcome that never exercises real local models and artifact lifecycle end to end
- Blind spots considered: compatibility of the external plan with the current repository; actual availability/names of DeepSeek profiles; platform/runtime prerequisites; hidden cross-workstream interfaces; local-model/runtime absence; safe export/import semantics; testability of desktop behavior
- Existing plan facts: `00_READ_FIRST.md` is controlling; every referenced ZANA file must be followed recursively; Appoint Me Agent Instructions and Handoffs must be inspected first and relevant architecture adopted; DeepSeek V4/Flash/Max profiles exposed through the installed Codex/OpenRouter/OpenCode setup must be visible first-class Codex agents where actually available; parallel work requires strict non-overlapping ownership/worktrees/handoffs; no mockups/placeholders/fake data; mandatory criteria live in `25_ACCEPTANCE_CRITERIA.md`; continue through implementation, integration, tests, fixes, and validation

## Goal Kind

`existing_plan`

## Current Tranche

Continuous execution: preserve accepted product contracts as migration truth, establish a production Rust Core, port the complete ZANA lifecycle without semantic regression, add the Android runtime/import/action plane, validate one explicitly approved small mobile model when its license and access are available, remove Python from the shipped authoritative path, and close only after final cross-platform acceptance.

## Non-Negotiable Constraints

- Treat `/Users/sero/Downloads/ZANA_BUILD_PLAN_DETAILED/00_READ_FIRST.md` as the controlling specification and recursively read every ZANA file it references; the originally supplied path does not exist.
- Before product implementation, inspect and adopt the relevant agent-management architecture from `/Users/sero/Documents/appointMe`, including Agent Instructions and Handoffs; the originally supplied spaced/capitalized path does not exist.
- Use the actually exposed DeepSeek agent profiles as first-class delegated agents; record any profile-name mismatch rather than inventing unavailable profiles.
- Parallel delegated writes must have proven disjoint file ownership or isolated worktrees and explicit handoffs; no agent may overwrite, revert, or delete another agent's work.
- Keep delegated agent communication quiet except for blockers, interface changes, and completion receipts.
- Do not substitute mocks, placeholders, fake data, or a UI-only prototype for real functionality.
- The final shipped authoritative Core is Rust-first. Do not add new Python product features or accept a Python runtime as the migration endpoint.
- Android is a deployment/runtime target, not a replacement for platform-neutral Images: medical, Kurdish/language-learning, private knowledge, tools, and other capabilities must use the same Image/Instance lifecycle.
- Android-native integration uses Kotlin/Jetpack APIs where required and a shared Rust Core; do not force Android system services, AppFunctions, lifecycle, or permissions through a webview-only abstraction.
- Do not place agent logic in a custom Linux kernel. OEM/AOSP system integration, if later enabled, remains a user-space/system-service target with explicit device authority.
- FunctionGemma model bytes may be downloaded and run only after exact license/access, digest, disk, memory, backend, and cleanup gates pass. Never bypass a gated model repository or silently substitute a different model.
- Preserve unrelated user changes in the workspace.
- Validate implementation against every mandatory item in `25_ACCEPTANCE_CRITERIA.md`.

## Stop Rule

Stop only when a final audit proves the full original outcome is complete.

Do not stop after planning, discovery, or Judge selection if a safe Worker task can be activated.

Do not stop after a single verified implementation slice while the broader owner outcome still has safe local follow-up slices.

If a slice needs credentials, a runtime, destructive permission, or owner input, block that exact slice with evidence and continue every safe local workaround and adjacent implementation task.

## Canonical Board

Machine truth lives at:

`docs/goals/zana-mvp/state.yaml`

If this charter and `state.yaml` disagree, `state.yaml` wins.

## Run Command

```text
/goal Follow docs/goals/zana-mvp/goal.md.
```

## PM Loop

On every continuation, read this charter and `state.yaml`; work only on the active task; delegate according to its exact scope; record a receipt; select the next safe task; and finish only after a Judge audit maps current implementation and verification to the original request with `full_outcome_complete: true`.
