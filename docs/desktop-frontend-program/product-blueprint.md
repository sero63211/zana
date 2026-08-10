# Product, UX, and Technical Desktop Blueprint

## 1. Product contract

ZANA is a local-first production desktop operator application. Its central work is the verified lifecycle:

`Capability Source -> exact Base Model and host analysis -> baseline -> approved Build Plan -> candidate -> evaluation gates -> immutable ZANA Image -> mutable ZANA Instance -> safe operation/export/import`

The frontend makes that lifecycle inspectable and controllable. It never becomes an alternate build engine, database, job scheduler, inference runtime, credential store, file processor, or source of product truth.

Primary audience: technical users, developers, internal AI teams, researchers, and privacy-sensitive operators managing local/self-hosted AI on one machine. V1 is localhost-first and macOS ARM64 is the first distributable target, while layout and semantics must remain portable to Windows/Linux.

## 2. Mandatory experience principles

1. **Truth before polish.** A blank catalog is a valid state. A guessed model, score, completion percentage, citation, compatibility result, or job success is a defect.
2. **Model-first, lifecycle-complete.** Users can begin from a discovered Base Model, but the UI always preserves the source/build/image/instance distinctions.
3. **Local is normal.** No account wall, cloud upsell, telemetry prompt, or mandatory online state. Offline is a first-class status with precise feature consequences.
4. **Every consequence is previewed.** Downloads, training, disk use, filesystem roots, network mode, tools, permissions, secrets, destructive changes, and missing dependencies appear before approval.
5. **Durable work survives navigation.** Long work lives in Core jobs. The global Jobs surface and contextual progress share one canonical snapshot/event model.
6. **Recovery is designed.** Error states name what remains safe, what may be retried, what needs operator action, and what must not be repeated automatically.
7. **Progress is evidence.** Named phase plus persisted event is primary; percentage is secondary and shown only when Core provides a valid fraction.
8. **Dense, calm, inspectable.** The application favors list/detail, tables, timelines, definitions, and diffs over oversized cards, decorative charts, glass effects, or "AI" theatrics.

## 3. Information architecture

Kimi owns the final navigation design. The product requirements map to this two-level architecture; any Kimi alternative must keep every destination reachable within two navigation levels and preserve deep-linkable selection.

| Group | Primary destination | Local substructure | Core user intent |
| --- | --- | --- | --- |
| Overview | Dashboard | readiness, active work, recent real events | Understand what is usable and what needs attention. |
| Discover | Models | runtimes, Base Models, acquisition | Find exact local model identities and manage endpoints. |
| Discover | System Doctor | checks, issues, feature readiness | Diagnose host/runtime/storage/training prerequisites. |
| Create | Capabilities | overview, behavior, knowledge, training, tools, permissions, evaluations | Author and validate editable Capability Sources. |
| Create | Builds | analysis, plan, approvals, progress, report | Turn exact inputs into a candidate and verified image. |
| Create | Evaluations | history, comparison, cases, gates | Inspect real baseline/candidate evidence. |
| Operate | ZANA Images | registry, detail, integrity, run/export/delete | Manage immutable verified/unverified artifacts. |
| Operate | Instances | list, runtime, chat, memory, image history | Operate mutable local AIs without mutating images. |
| Operate | Knowledge | sources, conversion, snapshots, retrieval evidence | Inspect immutable knowledge provenance and readiness. |
| Activity | Jobs | active, history, event detail, recovery | Monitor and control durable long-running operations. |
| Activity | Observability | local events, logs, diagnostics bundle | Diagnose operations without leaking secrets/content. |
| Activity | Resources | live snapshot, policy, leases, pressure | Understand admission decisions and host safety. |
| System | Settings | data, runtimes, network, training, privacy, logs, developer, updates | Configure persistent operator policy. |
| Contextual workflow | Export / Import | preflight, progress, report, missing dependencies | Move immutable OCI images safely. |

Export/Import is not a marketing-level primary destination. It is a first-class workflow launched from Images and the global command/action surface, with durable jobs also visible in Jobs.

## 4. Window and navigation behavior

- One main window in V1. Do not add utility windows until Tauri capability isolation, focus, ownership, and recovery are defined for each window.
- Current native bounds are 1180x760 with a 720x560 minimum. Kimi must reconcile the CSS 320 px reflow target with the native 720 px minimum: native layouts must remain fully usable at 720x560; the web test target must retain all content/functionality at 320 CSS px and 400% zoom.
- Restore last safe window geometry only after explicit window-state integration. Clamp off-screen, oversized, or obsolete monitor coordinates. Never restore a blocking dialog.
- Primary navigation keeps stable ordering and selection. Small-window behavior may collapse to an overlay/compact rail, but must not become a horizontally scrolling strip of 14 unlabeled destinations.
- Route identity must encode destination and selected stable id/digest; transient secrets, host paths, chat content, tokens, and approval payloads never enter the URL/hash.
- Back/forward preserves list filters and selection when safe. It does not replay mutations, approvals, chat sends, file picks, or destructive actions.
- A global command surface may expose navigation and safe read actions. Mutations still enter their screen-specific validation/confirmation flow.
- Closing the window while work runs does not imply cancellation. Show a truthful close choice only if the sidecar lifecycle cannot safely continue; describe whether jobs will be recovered, cancelled, or marked interrupted on next launch.

## 5. Desktop visual direction

Exact visual decisions belong to Kimi. Required character:

- Native-feeling macOS productivity workspace, portable rather than skeuomorphic.
- Cool neutral/graphite/slate surfaces with restrained semantic blue, success green, warning treatment that is not color-only, and danger red only for destructive/error states.
- System-forward typography; compact body and table density; readable code/digest typography; no serif brand flourish inside dense operator controls unless Kimi proves accessibility and coherence.
- 4/8 px spacing rhythm; modest radii; borders and grouping do more work than large shadows.
- Lists and tables are primary for catalogs. Summary cards exist only for a small set of actionable, real system facts.
- Charts are allowed only when a trend/comparison cannot be read faster from exact values. Every chart includes table/accessible text, real units, sample window, and source.
- Skeletons mirror stable layout for short reads. Long work uses phase/timeline/progress, never a page-size skeleton or endless spinner.
- Status always combines label, icon/shape, and semantic text. Digest and identity strength are visible where decisions depend on them.

## 6. Density and responsive behavior

| Width/context | Required behavior |
| --- | --- |
| >= 1180 px | Persistent grouped sidebar; list/detail can share the workspace; inspector/drawer may be side-by-side if focus order remains logical. |
| 900-1179 px | Compact sidebar; two-column facts collapse selectively; preserve list/detail with a narrower inspector or route-level detail. |
| 720-899 px native | Compact/overlay navigation; one primary reading column; tables gain column priority, disclosure, or card-row adaptation; no essential horizontal page scroll. |
| 320-719 px web/a11y test | Single column; navigation overlay; dialogs fit viewport; data tables expose each header/value association; all actions remain reachable. |
| Zoom/large text | No clipped labels, fixed-height text containers, hover-only disclosure, or status that disappears when rows grow. |

User density may have `Comfortable` and `Compact` settings later, but compact cannot shrink hit targets/focus indicators below accessibility requirements.

## 7. Global application state model

Never encode one `isLoading` boolean for the application. Maintain independent axes:

### 7.1 Core session

`STARTING -> CONNECTED`

Alternative states: `UNREACHABLE`, `UNAUTHORIZED`, `INVALID_RESPONSE`, `VERSION_MISMATCH`, `RESTARTING`, `REPLACEMENT_BLOCKED`, `STOPPED_UNEXPECTEDLY`.

Only Tauri can restart Core. Repeated automatic restart loops are forbidden. A replacement-blocked supervisor state requires app restart and must not pretend retry succeeded.

### 7.2 Read model

`IDLE -> LOADING -> READY_EMPTY | READY_POPULATED`

From ready: `REFRESHING` retains last confirmed content with explicit stale timestamp. Failure becomes `ERROR_NO_DATA` or `ERROR_WITH_STALE_DATA`. Retrying a read consumes `AbortSignal`; abort is not an error toast.

### 7.3 Mutation

`IDLE -> EDITING -> VALIDATING -> CONFIRMING (when consequential) -> SUBMITTING -> ACCEPTED`

Alternative states: `INLINE_INVALID`, `PERMISSION_REQUIRED`, `UNSUPPORTED`, `CONFLICT`, `REJECTED`, `UNCERTAIN`. `ACCEPTED` means Core accepted or queued the command, not that a durable job succeeded.

### 7.4 Durable job

Generic: `PENDING -> RUNNING -> SUCCEEDED | FAILED | CANCELLED`.

Cancellation: `RUNNING -> CANCEL_REQUESTED -> CANCELLED`; if Core has no intermediate state, the UI maintains only a clearly ephemeral "request sent" label until the next canonical snapshot. It may not overwrite Core status.

Build: use exact Core phases and terminal states; see the screen specification. `BLOCKED`, `FAILED`, `CANCELLED`, and `VERIFICATION_FAILED` are distinct and have different recovery.

### 7.5 Connectivity and permission

- App network: `LOCAL_ONLY`, `ACQUISITION_TEMPORARILY_ALLOWED`, `OFFLINE`, `REMOTE_ENDPOINT_EXPLICIT`.
- Runtime: per record `UNKNOWN`, `ONLINE`, `OFFLINE`, `ERROR`, plus evidence such as installed/not-running in metadata.
- Permission: `NOT_REQUESTED`, `EXPLAINED`, `GRANTED_FOR_EXACT_PLAN`, `DENIED`, `EXPIRED`, `CHANGED_INVALIDATES_GRANT`.

Browser `navigator.onLine` is never proof that a local runtime or external endpoint works.

## 8. Common state presentation contract

Every destination implements these states where applicable:

| State | Presentation and behavior |
| --- | --- |
| Loading | Named object and source; skeleton for short bounded lists, phase panel for work; status live region without focus theft. |
| Empty | Explain why emptiness is valid, next safe action, and prerequisite; no zero metric masquerading as health. |
| Validation | Inline at the field/source/case; preserve user input; summary links to invalid items; dialog is not used for routine validation. |
| Permission | Explain exact scope, reason, duration, and consequence of denial; approval binds to exact plan/digest where Core supports it. |
| Unsupported | Name hardware/runtime/provider/schema limitation and safe fallback. Do not offer an action that Core will guess. |
| Offline | State what still works, what is paused/blocked, and whether cached data is stale; no automatic download. |
| Streaming | Batch visual updates, preserve last durable cursor, provide phase and elapsed time, and separate disconnected UI from running job. |
| Cancel | Show request/acknowledgment separately; keep logs/events; describe partial-artifact cleanup and last good immutable state. |
| Retry | Retry only idempotent reads or Core-declared recoverable actions. Mutations require idempotency/operation identity or explicit new job. |
| Recovery | Name last safe state, retained evidence, required operator action, and whether resume/rebuild/new job is allowed. |
| Failure | Fixed safe error title, bounded Core message/code, correlation/job id, recovery actions, and local-log route; never raw exceptions. |
| Conflict/stale | Refresh canonical data, show what changed, and require revalidation/reapproval. Never last-write-wins a build plan or destructive action. |

## 9. Component hierarchy

Kimi owns final names/APIs. The hierarchy must preserve these responsibilities:

```text
AppBoundary
  CoreSessionProvider
  QueryClientProvider
  Router
  DesktopShell
    WindowChromeRegion
    SkipLink
    PrimaryNavigation
    CoreAndOfflineStatus
    GlobalJobIndicator
    RouteOutlet
      ScreenHeader / ScreenActions
      QueryStateBoundary
      ListDetailLayout
      CatalogTable / VirtualList
      Inspector / Tabs
      StatePanel / InlineError / RecoveryPanel
      JobTimeline / ProgressSummary / EventLog
      ApprovalReview / PermissionMatrix
      ConfirmationDialog
      NativePathPickerTrigger
      Digest / Identity / Provenance / SourceReference
      EmptyState / UnsupportedState / OfflineState
    ToastRegion (bounded, noncritical only)
    ModalLayer
```

Architecture boundaries:

- `api/`: generated or contract-owned wire types, runtime validators, error envelopes, fetch-stream parser, request ids. No React.
- `features/`: per-domain queries, mutations, route screens, and presentation mapping. A feature cannot import another feature's internal view components.
- `components/`: shared accessible primitives with no ZANA business-state guessing.
- `stores/`: ephemeral UI preferences only (selected tab, panel widths, draft view state). No server entities, tokens, job truth, or durable permission grants.
- `tauri/`: minimal typed wrappers for approved native commands/dialogs/window state. No general shell or filesystem API.
- `styles/`: design tokens, density, layout, focus, forced-colors/reduced-motion rules.

TanStack Query owns server cache. Core/SQLite owns persistence. Draft forms must explicitly save; unsaved frontend edits are labeled and protected from accidental navigation.

## 10. API and streaming integration

- Resolve `baseUrl` and per-launch token from Tauri memory. Send token only in `Authorization`; never store in Web Storage, IndexedDB, query strings, analytics, error objects, screenshots, or clipboard.
- Runtime-validate every untrusted JSON response and reject malformed required arrays/enums/numbers. Unknown fields are ignored unless the raw-metadata inspector has a separately bounded, redacted contract.
- Generate types from a committed OpenAPI/schema artifact once Core exposes a stable generation path. Until then, one contract owner maintains wire types plus runtime validation; do not hand-copy parallel representations.
- Use authenticated `fetch` for persisted event pages. Parse SSE incrementally with byte/event/page caps. Store only a bounded session window; Core remains history truth.
- Resume with exact last accepted event id. Reconcile every page/disconnect against `GET /jobs/{id}`. An empty page means "no new persisted events," not success.
- Apply bounded backoff with jitter for read reconnect; stop on auth/version errors and terminal job snapshot. No permanent high-frequency polling.
- Stream chat separately from job events. Batch tokens into animation-frame or <=10 Hz render commits; announce message completion, tool request, retrieval, and errors, not every token.
- Every mutation gets an operation identity/idempotency design before automatic retry. Without it, failure after submission is `UNCERTAIN`; offer refresh/reconcile, not blind resubmit.

## 11. Filesystem and persistence boundaries

- User approval begins with a native Tauri picker. The frontend receives the chosen path only long enough to submit a typed request; it displays basename/location summary, not unrestricted absolute paths.
- Core revalidates approval, file type, size, symlink containment, workspace boundary, digest, and copy. Picker approval is not backend authorization.
- Document ingestion copies approved input; originals are never modified. Source state distinguishes selected, copying, copied/hashed, validating, conversion warning, rejected, and removed-from-draft.
- Export uses native save/folder selection, then Core writes atomically through its portability service. Never download an archive through the webview or build it in browser memory.
- Import uses native file selection, Core temporary extraction, digest/schema/path validation, atomic registration, and cleanup evidence. Never preview archive HTML or execute embedded content.
- SQLite backup/export must coordinate with Core and WAL/checkpoint behavior. The frontend never copies the DB file.
- No automatic immutable artifact garbage collection in V1. Disk cleanup views may inspect and explain; deletion stays reference-aware and explicit.

## 12. Resource safety

Resource truth comes from Core snapshots, policies, admission decisions, and leases. UI requirements:

- Show available/total memory and disk with observation time and unknown/error states.
- Present configured safety reserve/fraction and operation-specific estimate as separate values.
- A blocked operation lists exact denial reason and Core-provided recovery actions; UI never recomputes or overrides admission.
- Active leases name category, owner/job, reserved memory/disk/workers, start/expiry, and cancellation availability without exposing sensitive payloads.
- Pressure does not trigger automatic deletion, model stop, training downgrade, or policy relaxation.
- Resource charts use real samples with retention/window labels. If only a point snapshot exists, render exact facts, not a trend line.
- No background UI polling. The future resource endpoint must define bounded sampling, freshness, and subscription/poll policy.

## 13. Security and privacy

- Tauri capability files are window-specific and least privilege. New custom commands are allowlisted in the app manifest before any additional window/webview exists.
- CSP stays restrictive: bundled scripts/styles/assets, loopback API only, no remote scripts, no inline script relaxation. Manual remote runtimes are called by Core, not the webview.
- Core and native errors are sanitized. UI error logs include code/status/correlation only; never serialize the original exception or response body.
- Capability documents, retrieved evidence, model output, imports, and runtime metadata are untrusted display data. Render as text; no HTML injection.
- Tools are default-deny and permission checked in Core. UI approval does not execute a tool and cannot bypass the decision engine.
- Secrets live in OS credential storage through a dedicated native/Core contract. The frontend sees reference/availability state, never secret values.
- Telemetry is fixed off for V1. Observability is local-only, bounded, redacted, and opt-in for any diagnostic export.
- Destructive operations use a review step with target identity, impact, blockers, and safe alternative. Images referenced by Instances cannot be deleted. Reset scopes remain distinct.

## 14. Keyboard and accessibility contract

- Logical DOM/focus order matches visual order. Sidebar arrow-key roving focus is optional; Tab must always work.
- Platform shortcuts: `Cmd/Ctrl+,` Settings; `Cmd/Ctrl+K` command/navigation if implemented; `Cmd/Ctrl+F` local filter when focus is in a catalog; `Cmd/Ctrl+Enter` only for explicitly labeled safe submit; Escape closes noncritical overlays and cancels drafts, never cancels a Core job.
- Tables expose headers, sortable state, filter labels, row selection, and keyboard-operable disclosure. Virtualized rows retain accessible position/count semantics.
- Modal dialogs implement APG focus containment and restoration. Irreversible confirmation initially focuses the safe choice.
- Live regions: `polite` for status/progress/completion; `assertive` only for blocking immediate errors. Streaming tokens are not live-announced individually.
- Contrast target WCAG AA; focus indicator visible on every surface; status not color-only; minimum pointer target 24x24 CSS px with adequate spacing, with 44 px preferred for primary controls.
- Support reduced motion, forced colors/high contrast, 200% text zoom, 400% page zoom/reflow, VoiceOver on macOS, and screen-reader names/descriptions for icon-only controls.
- Source excerpts and logs remain selectable text. Copy actions announce success without moving focus.

## 15. Performance budgets

These are engineering acceptance budgets, measured on a documented macOS ARM64 reference host and repeated on the lowest supported host. They are not marketing claims.

| Surface | Initial budget |
| --- | --- |
| App shell | First useful paint <= 1.0 s warm and <= 2.0 s cold p95, excluding Core readiness; shell remains interactive while Core starts. |
| Core readiness presentation | Connected or actionable failure visible <= 5 s p95; no unbounded startup spinner. |
| Route interaction | Cached route switch/selection feedback <= 100 ms; no steady main-thread task >50 ms. |
| Frontend payload | Initial route JavaScript <= 300 KiB gzip; domain-heavy routes lazy loaded; no general chart/editor framework without measured need. |
| Catalogs | Virtualize beyond 200 visible rows; <=1,500 live row/detail DOM nodes; local filter response <=100 ms for 10,000 lightweight rows. |
| Streaming | Visual token/event commits <=10 Hz; buffered unrendered data <=256 KiB; per-open-job retained UI events <=1,000 or 4 MiB, whichever first. |
| Reads | <=4 concurrent non-stream reads by default; superseded reads abort; no interval poll shorter than 5 s and no polling at all unless a contract explicitly requires it. |
| Memory | Frontend/webview idle target <=150 MiB and <=50 MiB growth after a 30-minute bounded workflow; record platform variance and investigate sustained growth. |
| Layout | No essential horizontal page scroll at 720 px native or 320 px browser reflow; no cumulative layout shift from late status content after stable skeleton. |

If a budget is infeasible, Kimi records measurement, cause, user impact, and an approved replacement budget. Silent deletion of a budget is forbidden.

## 16. Design acceptance method

Kimi is the final visual acceptance owner and must:

1. Build a state inventory from real contracts and test-only typed fixtures that can never ship or feed production state.
2. Review every screen at 1440x900, 1180x760, 900x700, 720x560, 320x800 web reflow, 200% text, reduced motion, and forced-colors/high contrast where supported.
3. Capture deterministic screenshots for loading, empty, populated, validation, permission, unsupported, offline/stale, streaming, cancellation, failure, and recovery.
4. Perform keyboard-only and VoiceOver passes, including focus restoration after every modal.
5. Compare visual hierarchy, density, semantic colors, truncation, long translated/hostile strings, and unknown metadata behavior.
6. Reject any production component that depends on fabricated metrics, a hidden disabled action, hover-only content, an unbounded list, or raw backend detail.
7. Record final acceptance with exact build SHA, fixture/schema version, viewport, OS, and unresolved visual risk.

## 17. Explicit non-goals

No cloud account, public marketplace, billing, Kubernetes console, arbitrary shell, LAN discovery, automatic third-party runtime start, automatic model download, remote telemetry, mobile client, social/community surface, proprietary artifact format, cloud LLM judge, or fully bundled model weights by default. This program does not add any of them.
