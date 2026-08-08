# ZANA MVP End-to-End Build

## Objective

Build and verify the complete real ZANA MVP from the controlling build-plan documents, adopting the relevant Appoint Me agent-management architecture first and continuing through integration, testing, repair, and final acceptance.

## Original Request

Execute `/Users/sero/Downloads/ZANA_BUILD_PLAN_DETAILED/00_READ_FIRST.md` (the corrected existing path), recursively follow every referenced ZANA file, adopt agent-management conventions from `/Users/sero/Documents/appointMe` (the corrected existing path), then build the functional desktop UI, backend, real local-model discovery, capability build/evaluation, ZANA Image/instance, and export/import flow until every mandatory criterion in `25_ACCEPTANCE_CRITERIA.md` passes.

## Intake Summary

- Input shape: `existing_plan`
- Audience: ZANA desktop users and the owner/operator
- Authority: `approved`
- Proof type: `test`
- Completion proof: every mandatory acceptance criterion passes against real local behavior, with reproducible verification for the full desktop and backend workflow and a final Judge audit recording `full_outcome_complete: true`
- Likely misfire: producing a polished mockup, partial scaffold, fake-data demo, or plan-only outcome that never exercises real local models and artifact lifecycle end to end
- Blind spots considered: compatibility of the external plan with the current repository; actual availability/names of DeepSeek profiles; platform/runtime prerequisites; hidden cross-workstream interfaces; local-model/runtime absence; safe export/import semantics; testability of desktop behavior
- Existing plan facts: `00_READ_FIRST.md` is controlling; every referenced ZANA file must be followed recursively; Appoint Me Agent Instructions and Handoffs must be inspected first and relevant architecture adopted; DeepSeek V4/Flash/Max profiles exposed through the installed Codex/OpenRouter/OpenCode setup must be visible first-class Codex agents where actually available; parallel work requires strict non-overlapping ownership/worktrees/handoffs; no mockups/placeholders/fake data; mandatory criteria live in `25_ACCEPTANCE_CRITERIA.md`; continue through implementation, integration, tests, fixes, and validation

## Goal Kind

`existing_plan`

## Current Tranche

Continuous execution: validate and operationalize the supplied architecture and specification, complete successive safe verified implementation slices, integrate them, exercise the real end-to-end workflow, and close only after the final acceptance audit proves the full owner outcome.

## Non-Negotiable Constraints

- Treat `/Users/sero/Downloads/ZANA_BUILD_PLAN_DETAILED/00_READ_FIRST.md` as the controlling specification and recursively read every ZANA file it references; the originally supplied path does not exist.
- Before product implementation, inspect and adopt the relevant agent-management architecture from `/Users/sero/Documents/appointMe`, including Agent Instructions and Handoffs; the originally supplied spaced/capitalized path does not exist.
- Use the actually exposed DeepSeek agent profiles as first-class delegated agents; record any profile-name mismatch rather than inventing unavailable profiles.
- Parallel delegated writes must have proven disjoint file ownership or isolated worktrees and explicit handoffs; no agent may overwrite, revert, or delete another agent's work.
- Keep delegated agent communication quiet except for blockers, interface changes, and completion receipts.
- Do not substitute mocks, placeholders, fake data, or a UI-only prototype for real functionality.
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
