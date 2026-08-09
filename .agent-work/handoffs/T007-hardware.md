# Handoff T007-hardware - Cross-Platform Hardware Profile

Verdict: PASS

## Scope

Implemented the non-privileged cross-platform `HardwareProfile` lane from
specs `07_HARDWARE_PROFILER_AND_BUILD_PLANNER.md`,
`16_SECURITY_AND_PERMISSIONS.md`, `21_REPOSITORY_STRUCTURE.md`,
`22_TESTING_AND_QA.md`, and `25_ACCEPTANCE_CRITERIA.md`. No build planner,
database wiring, API registration, product UI, or cross-lane interface was
changed.

## Changed files and touched modules

Implementation (`core/zana_core/hardware/**`):

- `__init__.py` - public hardware profile exports
- `models.py` - typed OS/arch/CPU/memory/disk/accelerator/backend structures
- `commands.py` - injectable bounded command runner with timeout and non-fatal
  error capture
- `providers.py` - macOS, Linux, Windows, and unknown platform provider
  boundaries using `platform`, `psutil`, `shutil`, bounded `sysctl` /
  `system_profiler` / `wmic` where needed
- `nvidia.py` - optional `nvidia-smi` probe, only when the executable exists,
  with timeout and non-fatal parsing/failure
- `backends.py` - training/runtime backend availability from installed
  executables/modules only; nothing is started
- `profile.py` - `collect_profile` composition with injectable runner,
  executable probe, and clock

Tests (`core/tests/hardware/**`): 54 focused tests covering platform variants,
command absence/failure/timeout/malformed output, NVIDIA parsing and probe
failure modes, disk paths, memory values, backend availability, no-admin
behavior, and real-host Apple Metal probing on this machine.

Handoff: `.agent-work/handoffs/T007-hardware.md` (this file).

## Interface facts

- `HardwareProfile` is a Pydantic model with deterministic
  unavailable/unknown representation: unknown values stay `None`, absent
  accelerators/backends are empty or `installed: false`, and notes carry only
  probe-level uncertainty.
- Backend kinds reuse the T005 `RuntimeKind` string values
  (`ollama`, `lm-studio`, `llama.cpp`, `mlx-lm`) plus `hf_peft`; a later
  integration lane can map them without renegotiating the contract.
- Apple Metal detection: on `arm64` macOS, Metal is reported with
  `shared_memory: true` from the platform itself; on x86_64 macOS, Metal
  support comes from a bounded `system_profiler` query and shared-memory
  semantics stay unknown rather than invented.
- NVIDIA: `nvidia-smi` is probed only when the executable is found. CSV
  parsing accepts `nounits` (MiB) values, suffixed values, and `N/A`; any
  absence, timeout, exit failure, or malformed output is non-fatal and
  recorded as a note.
- Training/runtime availability is derived only from `shutil.which` results
  and `importlib.util.find_spec`, never by importing or starting backends.
- No build planner, database, or API wiring exists in this lane.

## Checks run and evidence

All commands used the existing shared environment at
`/Users/sero/Documents/zana/core/.venv/bin`; no dependencies were installed
and no new venv was created.

| Check | Command | Result |
| --- | --- | --- |
| Focused tests | `python -m pytest core/tests/hardware -q` | 54 passed |
| Full Core suite | `python -m pytest core/tests -q` | 91 passed |
| Ruff lint | `ruff check core/zana_core/hardware core/tests/hardware` | clean |
| Ruff format | `ruff format --check core/zana_core/hardware core/tests/hardware` | clean |
| Pyright | `pyright core/zana_core/hardware` | 0 errors, 0 warnings |
| Diff hygiene | `git diff --check` | pass |
| Real-host profile | `collect_profile` against the live host via tests | memory/disk/cores positive; Apple Metal detected |

## Security delta

- No privileged operations: all probes are read-only and bounded; no command
  elevates privileges or touches paths outside the explicit workspace probe.
- Backend probes never start processes; they only resolve installed paths and
  Python module specs.
- Subprocess output is decoded with replacement errors and captured, never
  leaked into logs or responses (no logging or API surface was added).
- No secrets, model files, or user data are accessed or recorded.

## Residual risk

- Real Linux and Windows provider behavior is covered by unit tests and fake
  command boundaries; live validation on those OSes remains for integration.
- `system_profiler` JSON structure may vary across macOS releases; failures
  are non-fatal and produce a note rather than a fabricated accelerator.
- Backend availability proves installation, not that a server is running or
  that a model is present; runtime/model discovery remains a separate lane.

## Blockers

None.

## Commit and merge instructions

- Implementation commit: `a84204f` (`feat: add cross-platform hardware
  profiler`) on branch `agent/T007-hardware`, started exactly at integrated
  commit `9e36e4c` (`master`).
- This handoff is committed separately on the same branch.
- Merge `core/zana_core/hardware/**`, `core/tests/hardware/**`, and this
  handoff through the PM integration lane. No lockfile or shared manifest
  change is included; existing dependencies (`psutil`, `pydantic`) already
  cover this lane.
- Deferred verification: live Linux/Windows profiling and full acceptance
  smoke against the bundled application are outside this lane and remain for
  the PM integration and later runtime/build lanes.
