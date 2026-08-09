# T900 Desktop Models Handoff

Verdict: PASS

## Scope

Replaced the static Home, Runtimes & Models, and Settings & Doctor surfaces with
a typed, authenticated, model-first desktop product vertical wired to the
frozen Core API at canonical base `6812372ec220502d4bbc6812135903f242baec6e`.

Worktree: `/Users/sero/.codex/worktrees/0436/zana`
Branch: `agent/t900-desktop-models`
Base: `6812372ec220502d4bbc6812135903f242baec6e`
Remote: `origin` (`https://github.com/sero63211/zana.git`); no push attempted.

Only exclusive owned paths were changed:

- `apps/desktop/src/api/client.ts`, `client.test.ts`, `format.ts`, `types.ts`
- `apps/desktop/src/hooks/useCoreApi.ts`, `useCoreHealth.ts`
- `apps/desktop/src/views/HomeView.tsx`, `HomeView.test.tsx`
- `apps/desktop/src/views/RuntimesModelsView.tsx`, `RuntimesModelsView.test.tsx`
- `apps/desktop/src/views/SettingsDoctorView.tsx`, `SettingsDoctorView.test.tsx`
- `apps/desktop/src/styles/app.css`
- `apps/desktop/src/test/render.tsx`
- `.agent-work/handoffs/T900-desktop-models.md`

No unowned file, manifest, lockfile, Tauri/Rust, Core/Python, navigation, icon,
or coordination source was modified.

## Changed behavior

- `apps/desktop/src/api/client.ts`: reusable authenticated Core client over
  `resolveCoreConnection`; canonical `CoreApiError` envelope parsing; no raw
  backend exceptions; `AbortSignal` support; strict hand-rolled runtime
  validation of untrusted JSON; correct path/query encoding including
  slash-preserving model keys; endpoints for system profile/doctor, runtimes,
  runtime refresh, manual add, manual-only delete, models with filters, model
  detail, and approved model pull.
- `apps/desktop/src/api/types.ts` and `format.ts`: typed response models and
  read-only formatting/status helpers; unknown fields are never rendered.
- `apps/desktop/src/hooks/useCoreApi.ts`: React Query hooks/mutations for all
  owned endpoints; refresh invalidates only runtimes/models; add invalidates
  runtimes; delete invalidates runtimes/models; pull invalidates nothing
  because the backend only records a queued plan.
- `apps/desktop/src/views/HomeView.tsx`: model-first dashboard with real
  runtime/model/hardware/doctor summaries, loading/empty/error states, retry,
  and direct routes to Models and Doctor.
- `apps/desktop/src/views/RuntimesModelsView.tsx`: real discovery refresh,
  runtime records with online/offline/installed-not-running/manual states,
  model descriptors with only returned metadata, manual endpoint add, explicit
  two-step delete for manual entries only, and a deliberate-approval Ollama
  pull form that truthfully reports the returned queued job.
- `apps/desktop/src/views/SettingsDoctorView.tsx`: real aggregate health, every
  returned check, redacted evidence, issues, safe recovery actions, and the
  hardware profile; token is never rendered.
- `apps/desktop/src/styles/app.css`: removed warm/orange tokens and replaced
  them with a restrained cool blue on cool white/graphite/slate surfaces; added
  dashboard, runtime/model, form, notice, badge, doctor, and accessibility
  styles; reduced-motion support retained.
- `apps/desktop/src/hooks/useCoreHealth.ts`: removed the background 5s poll;
  health is fetched on mount and refetched on explicit actions only.

## Checks run and evidence

Per the binding host-safety rule, only smallest focused unit tests plus bounded
static/type/lint/format/build checks were run. Existing dependencies were
reused read-only from the canonical checkout through temporary ignored
symlinks for local tooling; nothing was installed and the symlinks were removed
before commit.

| Check | Result |
| --- | --- |
| Focused Vitest: `src/api/client.test.ts`, `HomeView.test.tsx`, `RuntimesModelsView.test.tsx`, `SettingsDoctorView.test.tsx` | 23 passed |
| `pnpm --filter @zana/desktop typecheck` | PASS |
| `pnpm --filter @zana/desktop lint` | PASS |
| `pnpm --filter @zana/desktop build` (bounded Vite build) | PASS |
| `git diff --check` | PASS |

Focused tests cover real-data, loading, empty, canonical error/recovery,
refresh invalidation, manual add/delete, explicit pull confirmation and
payload, safe path encoding, doctor checks, and token non-rendering.

## Intentionally not run

Per Sero's binding host-safety rule, the following were intentionally not run:
full desktop test suite, full Core suite, live/provider/browser/app/device
tests, dev server, desktop launch, Tauri bundle or native build, runtime/model
start, inference, download, training, load, GPU/RAM, container, or performance
tests, and any network verification. No Ollama or local model was started.

## Security delta

- Per-launch token is sent only in the `Authorization` header; it is never
  rendered, persisted, or logged, and error messages never contain it.
- Canonical backend error envelopes are parsed into typed `CoreApiError`
  values; malformed/non-JSON responses are rejected with `INVALID_RESPONSE`,
  never surfaced as raw exceptions or unknown objects.
- Manual endpoints reject embedded credentials and non-http(s) URLs.
- Delete is rendered only for manual runtimes and requires a confirmation step.
- Pull requires the deliberate approval control and sends
  `user_approved: true`; the returned job is described exactly as queued, with
  no claim that bytes were downloaded or a model installed.
- Discovery refresh remains loopback-only and never starts a runtime.
- Runtime/model/doctor data is runtime-validated and only known fields are
  rendered; unknown object values are never shown.

## Residual risk

- The backend persists approved native pull plans but does not execute them;
  this UI truthfully reports the queued job and no bytes/install state.
- Health polling was removed; the shell Core indicator reflects mount-time and
  explicit refetch state rather than a background heartbeat.
- `useModel` (GET `/models/{key}`) is exposed as a typed hook but the current
  Models view renders the full returned descriptor from the list endpoint, so
  the detail endpoint is not separately surfaced in the UI.
- No live backend, app launch, or native verification was run by host rule.

## Blockers

None.

## Merge instructions

Integrate the single implementation commit and this handoff commit separately
onto the canonical lane at base `6812372`. No lockfile, manifest, Core/Python,
Tauri/Rust, navigation, icon, or GoalBuddy control file is included. After
integration, rerun the focused desktop tests plus bounded typecheck/lint/build
with the normal installed toolchain.

## Accepted commits and clean proof

- Product implementation commit: `278a1e91b779a1cb329f8315256278d5047acd90`
  (`feat: wire desktop models dashboard to Core API`)
- Receipt commit: this handoff commit (resolve with `git rev-parse HEAD`).
- Clean proof: `git status --porcelain` empty and `git diff --check` pass after
  the product commit; the receipt commit is the only additional change.
- Remote: `origin`; no push attempted. Explicit push blocker: lead
  integration owns remote promotion per ZANA policy.
