# T007 Evaluation Handoff — Deterministic Scorers, Aggregation, and Gates

Verdict: PASS

## Scope

Implemented the bounded deterministic evaluation foundation for ZANA. The
package is pure and never invokes a runtime or model. Only owned paths were
changed: `core/zana_core/evaluation/**`, `core/tests/evaluation/**`, and this
handoff. No API, database, schema file, cloud judge, inference, training,
embedding, or memory-heavy command was touched.

## Changed files and modules

- `core/zana_core/evaluation/models.py` — typed `EvaluationCase`,
  `ScorerConfig`, `ScorerResult`, `AggregateMetrics`, `EvaluationSuiteResult`,
  `BaselineCandidateComparison`, `ReproducibilitySettings`,
  `GateResult`/`GateDecision`, and `VerificationStatus`. Raw outputs and
  failure reasons are preserved on every result.
- `core/zana_core/evaluation/scorers.py` — real pure scorers: exact string,
  case-normalized exact, numeric exact, numeric tolerance, regex,
  contains-all, JSON Schema validity, classification label,
  citation-required, and source-grounding against known source ids. The JSON
  Schema scorer uses a small stdlib-only validator and rejects malformed or
  unsupported schemas; no `jsonschema` dependency is required.
- `core/zana_core/evaluation/aggregate.py` — deterministic aggregation with
  explicit empty, zero, and invalid-input handling.
- `core/zana_core/evaluation/gates.py` — fail-closed gate engine supporting
  absolute quality thresholds, minimum improvement, maximum allowed
  regression/drop, and no-gate fail-closed behavior. A capability is verified
  only when all declared gates pass.
- `core/zana_core/evaluation/heldout.py` — held-out identifier isolation check
  between evaluation case ids and training identifiers.
- `core/tests/evaluation/**` — 39 focused tests covering every scorer and edge
  case, malformed JSON/schema, citations/source ids, aggregate math, PASS/FAIL
  gates, regression prevention, configuration capture, and held-out isolation.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused pytest | `python -m pytest core/tests/evaluation -q` | 39 passed |
| Full Core pytest | `python -m pytest core/tests -q` | 224 passed, 1 unrelated pre-existing hardware probe failure (`test_darwin_metal_real_probe`, not owned surface) |
| Ruff lint | `ruff check core` | clean |
| Ruff format | `ruff format --check core` | clean |
| Pyright | `pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

Verification reused the existing shared Core virtualenv at
`/Users/sero/.codex/worktrees/216c/zana/core/.venv` with
`PYTHONPATH=/Users/sero/.codex/worktrees/ba7c/zana/core`; no dependencies were
installed and no model/runtime was started.

## Security delta

- No runtime invocation, inference, embedding, training, or external judge.
- JSON Schema validation rejects unsupported keywords and malformed schemas
  instead of silently accepting arbitrary documents.
- Held-out isolation prevents evaluation ids from leaking into training
  identifiers.
- No secrets, private document contents, or raw private data are emitted by
  the gate or aggregate records; failures preserve only output strings and
  structured reasons.

## Residual risk

- The JSON Schema validator intentionally implements a small stdlib-only
  Draft-2020-12 subset. If future evaluation suites require advanced schema
  keywords, this lane must either extend the validator or adopt an existing
  dependency under a PM-approved manifest change.
- The full-suite hardware probe failure is environmental to this host and
  unrelated to the owned surface; it is reported for transparency and should
  not block this handoff.
- This package is a pure domain foundation; runtime evaluation wiring,
  baseline/candidate orchestration, and report persistence are later lanes and
  must call these primitives.

## Blockers

None.

## Commit and cherry-pick instructions

- Implementation commit: `358529e`
  (`feat: add deterministic evaluation scorers and gates`) on branch
  `agent/T007-evaluation`, started exactly at integrated commit `830ab2f`.
- This handoff is committed separately on the same branch.
- Cherry-pick `358529e` and the handoff commit onto the PM integration branch
  in that order. No lockfile, manifest, schema file, or other lane path is
  included.
