# T900 MLX Training Execution Handoff

Verdict: PASS

## Outcome

The bounded MLX-LM training execution vertical is integrated on canonical
`main` as `254bf10d915991f3e18b4a38b1f945f7a858be9d` and pushed non-force to
`origin/main`. The canonical product commit contains only the accepted training
implementation and focused tests; this file is part of the separate receipt.

## Changed modules

- `core/zana_core/training/**`
- `core/tests/training/**`

## Verification

- `core/.venv/bin/python -m pytest tests/training -q`: 153 passed.
- Ruff check and format check on `zana_core/training` and `tests/training`: pass.
- Pyright on `zana_core/training`: 0 errors, 0 warnings, 0 informations.
- `import zana_core.training`: pass.
- `git diff --check`: pass.
- Forbidden real-process scan found no `sys.executable` or long-sleep training
  fixtures.
- The product tree was compared byte-for-byte with the accepted recovery branch.
- Three local lead review passes covered process lifecycle, setup/error cleanup,
  sanitization, staging/held-out isolation, workspace ownership, retry cleanup,
  and replacement/tamper paths.
- `codex review --base ...` was attempted but denied by the source-egress safety
  guard; that boundary was not bypassed. Local inspection and focused gates were
  used instead.

## Security delta

- Offline, exact MLX-LM executable/provider/base-model identity and bounded argv.
- Held-out evaluation data cannot enter training staging or invocation.
- Private attested workspaces, bounded no-follow source staging, capped logs,
  bounded adapter verification, and sanitized path/secret diagnostics.
- POSIX process-group TERM then KILL with independent parent, group, and drain
  proof on timeout, cancellation, setup failure, wait failure, and surviving
  descendants after normal parent exit.
- Log cleanup is bound to the request-created sink inode.
- Fresh construction cleanup is identity-bound and non-recursive; unexpected or
  nonempty content is retained behind a verified retry attestation. Replaced
  workspaces receive no marker or cleanup handle.

## Residual risk

- No real MLX import/process, model load/download, training run, live provider,
  runtime, API, desktop app/browser, broad suite, bundle, performance, or load
  verification ran under the current host-safety policy.
- Real provider output and operating-system process-group behavior require a
  later explicitly approved bounded live verification session.

## Commits and remote receipt

- Accepted canonical product commit: `254bf10d915991f3e18b4a38b1f945f7a858be9d`.
- Final recovery product commits: `ebec1f6a545a022df3c28586b725c3ef2ca37f37`
  and `29bfa8784eb8ffeef153e0e349e4c68308983b30`.
- Remote proof: `refs/heads/main`, local `HEAD`, and `origin/main` all resolved to
  `254bf10d915991f3e18b4a38b1f945f7a858be9d` after the non-force push.
- Canonical and recovery worktrees were clean after their accepted commits.

## Next serial scope

Resume `T900-model-acquisition-execution` only after this receipt is committed
and pushed. Keep all transport tests injected; do not start a runtime, network
pull, model download, model process, or live API.
