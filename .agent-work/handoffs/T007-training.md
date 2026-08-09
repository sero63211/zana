# T007 Training Handoff — Safe Non-Executing Training Foundation

Verdict: PASS

## Scope

Implemented the bounded, non-executing training foundation for ZANA. The
package is pure and never imports ML frameworks, starts providers, runs
inference, downloads artifacts, executes training commands, or contacts a
runtime. Only owned paths were changed: `core/zana_core/training/**`,
`core/tests/training/**`, and this handoff.

## Changed files and modules

- `core/zana_core/training/contracts.py` — strict immutable typed contracts:
  inference identity, training-source identity, adapter-base identity,
  dataset split manifests/hashes, provider probe, compatibility decision,
  training request/config, resource guard decision, invocation spec, run
  record, cancellation/partial-artifact state, adapter metadata, and
  materialization compatibility.
- `core/zana_core/training/identity.py` — exact identity enforcement; a
  runtime display name can never establish a trainable/adapter base.
- `core/zana_core/training/providers.py` — metadata-only MLX-LM and HF PEFT
  probes via injected module/version/executable/platform inspectors; probe
  failure is structured and non-fatal.
- `core/zana_core/training/datasets.py` — train/validation/evaluation split
  isolation by canonical digest and record ids; raw documents/books rejected
  as training targets; deterministic synthetic metadata with disjoint
  held-out seed/range.
- `core/zana_core/training/resources.py` — explicit RAM/VRAM/disk/dry-run
  allow/block/unknown guards; never guesses success or duration.
- `core/zana_core/training/invocations.py` — MLX-LM and HF PEFT invocation
  builders returning argv data only with allowlisted typed arguments, exact
  versions, seed, dataset/config hashes, output path, and environment
  metadata.
- `core/zana_core/training/cancellation.py` — cancellation/artifact state
  machine; partial outputs unusable, logs retained, terminal transitions fail
  closed.
- `core/zana_core/training/adapters.py` — safetensors path/type expectation,
  SHA-256 digest verification through injected/read-only verifier, exact base
  binding, provider/dataset/config/version provenance.
- `core/zana_core/training/materialization.py` — runtime materialization
  compatibility decisions only; no runtime contact.
- `core/tests/training/**` — 44 focused tests covering provider probing,
  identity mismatch, data/eval leakage, document no-training rule,
  deterministic synthetic metadata, resource thresholds, command
  allowlisting/escaping-as-argv, cancellation transitions, partial promotion
  rejection, adapter provenance/digest verification with tiny temporary
  fixtures, and materialization compatibility.

## Checks run and evidence

| Check | Command | Result |
| --- | --- | --- |
| Focused pytest | `python -m pytest core/tests/training -q` | 44 passed |
| Full Core pytest | `python -m pytest core/tests -q` | 343 passed, 1 unrelated pre-existing hardware probe failure (`test_darwin_metal_real_probe`, not owned surface) |
| Ruff lint | `ruff check core` | clean |
| Ruff format | `ruff format --check core` | clean |
| Pyright | `pyright core/zana_core` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |

Verification reused the existing shared Core virtualenv at
`/Users/sero/.codex/worktrees/216c/zana/core/.venv` with
`PYTHONPATH=/Users/sero/.codex/worktrees/ba7c/zana/core`; no dependencies were
installed, no ML imports, and no model/runtime was started.

## Security delta

- No training/inference execution surface in this package; invocation specs
  are data only.
- Exact identity enforcement rejects display-name-only base claims.
- Provider probes use metadata only and fail structured/non-fatal.
- Dataset isolation prevents evaluation from entering training and raw
  documents/books are rejected as training targets.
- Cancellation transitions fail closed and partial adapters can never be
  promoted.
- Adapter validation requires safetensors path/type, real SHA-256 digest, and
  full provenance; no fake adapter bytes are created.

## Residual risk

- The full-suite hardware probe failure is environmental to this host and
  unrelated to the owned surface; it is reported for transparency and should
  not block this handoff.
- Invocation specs are intentionally not executed here; provider command-line
  contract verification must happen in a later execution lane against real
  installed provider packages.
- HF PEFT dry-run support is modeled through injected metadata; real provider
  capability confirmation belongs to the execution lane.

## Blockers

None.

## Commit and cherry-pick instructions

- Implementation commit: `2065a57`
  (`feat: add safe non-executing training foundation`) on branch
  `agent/T007-training`, started exactly at integrated commit `46abc1e`.
- This handoff is committed separately on the same branch.
- Cherry-pick `2065a57` and the handoff commit onto the PM integration branch
  in that order. No lockfile, manifest, schema file, or other lane path is
  included.
