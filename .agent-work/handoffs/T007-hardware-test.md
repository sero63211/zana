# Handoff T007-hardware-test - Darwin Probe Test Isolation Repair

Verdict: PASS

## Root cause evidence

Four tests in `core/tests/hardware/test_providers.py` created
`pytest.MonkeyPatch()` instances manually and never called `undo()`:

- `test_darwin_arm64_metal_without_brand_name`
- `test_darwin_x86_metal_parses_system_profiler`
- `test_darwin_x86_metal_profiler_failure_is_unknown`
- `test_linux_cpu_model_name_reads_proc_cpuinfo`

The `setattr("zana_core.hardware.providers.platform.machine", ...)` calls
patch the stdlib `platform` module attribute process-wide. Independently
proven:

1. `platform.machine()` returns `x86_64` after the manual patch and stays
   patched with no `undo()`; the providers module shares the same `platform`
   module object.
2. With the leak active, `DarwinProvider().platform_accelerators(...)` on real
   macOS ARM64 takes the x86 `system_profiler` branch instead of the
   `apple_silicon_platform` branch. On hosts where `system_profiler` omits
   `spdisplays_metal`, that branch returns zero accelerators, which is the
   observed full-Core failure.
3. The patched x86 branch also loses the platform-guaranteed
   `shared_memory: true` semantic and depends on subprocess output that the
   real probe should never need on Apple Silicon.

On this specific host the leaked x86 branch still happened to find one Metal
entry, so the zero-accelerator symptom is host-dependent; the isolation defect
and the wrong probe path were both reproduced deterministically.

## Change

Only `core/tests/hardware/test_providers.py` changed. The four manual
`MonkeyPatch()` instances were converted to pytest's fixture-scoped
`monkeypatch: pytest.MonkeyPatch` parameter so pytest undoes every patch after
each test. No provider/application code, other tests, or assertions were
changed or weakened; the real-probe assertion still requires a detected Metal
accelerator and `shared_memory: true` on the platform path.

## Checks run and evidence

All commands used the existing shared environment at
`/Users/sero/Documents/zana/core/.venv/bin`; nothing was installed or synced.

| Check | Command | Result |
| --- | --- | --- |
| Focused file | `python -m pytest core/tests/hardware/test_providers.py -q` | 23 passed |
| Leak-order sequence | `pytest ...::test_darwin_x86_metal_parses_system_profiler ...::test_darwin_metal_real_probe` | 2 passed in one process |
| Hardware suite | `python -m pytest core/tests/hardware -q` | 54 passed |
| Ruff lint | `ruff check core/tests/hardware/test_providers.py` | clean |
| Ruff format | `ruff format --check core/tests/hardware/test_providers.py` | clean |
| Diff hygiene | `git diff --check` | pass |

The leak-order sequence now completes in 0.06s versus 0.22s before the fix,
confirming the real probe returns to the `apple_silicon_platform` branch and
no longer shells out to `system_profiler`.

## Security delta

None. Test-only change; no production, subprocess, filesystem, or network
behavior changed. The real probe remains read-only and non-privileged.

## Residual risk

- The real-probe test is still skipped on non-Darwin/non-ARM64 hosts, so the
  arm64 branch remains unexercised there; that is unchanged behavior.
- `system_profiler` JSON shape remains a probe-level residual risk on x86_64
  macOS and is handled non-fatally by the provider.

## Blockers

None.

## Commit and cherry-pick instructions

- Implementation commit: `f2cfcfa` (`test: isolate Darwin platform probe
  monkeypatches`) on branch `agent/T007-hardware-test`, started exactly at
  base commit `f78e79c`.
- This handoff is committed separately on the same branch.
- Cherry-pick `f2cfcfa` and the handoff commit onto `master` through the PM
  integration lane. Only `core/tests/hardware/test_providers.py` and this
  handoff are included.
