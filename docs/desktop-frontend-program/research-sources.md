# Current Primary-Source Research

Access date for every source: **2026-08-10**.

Only first-party or standards-owner sources are included. The program adopts interaction principles and technical constraints, not competitor wording, layout, trade dress, or expression. HTTP availability was checked directly on the access date. Apple HIG pages are JavaScript-rendered but returned the official current page titles and successful responses.

## Desktop application patterns

| Official source | Direct URL | Program use |
| --- | --- | --- |
| Apple Human Interface Guidelines — Sidebars | https://developer.apple.com/design/human-interface-guidelines/sidebars | A persistent, selectable navigation rail is appropriate for top-level destinations; use familiar macOS hierarchy, clear selection, and calm density. |
| Apple HIG — Windows | https://developer.apple.com/design/human-interface-guidelines/windows | Treat resizing, title-bar integration, restoration, and minimum usable size as product behavior rather than CSS afterthoughts. |
| Apple HIG — Progress indicators | https://developer.apple.com/design/human-interface-guidelines/progress-indicators | Use determinate progress only when Core provides a trustworthy fraction; otherwise pair indeterminate activity with a named phase and elapsed state. |
| Apple HIG — Alerts | https://developer.apple.com/design/human-interface-guidelines/alerts | Reserve blocking alerts for consequential choices; make the consequence and safe alternative explicit. |
| Apple HIG — Accessibility | https://developer.apple.com/design/human-interface-guidelines/accessibility | Support keyboard, assistive technology, sufficient contrast, non-color status, and user display/motion preferences as default product quality. |
| Microsoft Windows App design — Navigation basics | https://learn.microsoft.com/en-us/windows/apps/design/basics/navigation-basics | Keep primary navigation consistent, simple, and clear; avoid deep hierarchies and use list/detail for operator catalogs. This informs cross-platform behavior without copying WinUI visuals. |
| Microsoft Windows App design — Progress controls | https://learn.microsoft.com/en-us/windows/apps/design/controls/progress-controls | Choose determinate vs indeterminate from real duration knowledge, keep non-blocking work non-modal, and always label the operation. |
| Microsoft Windows App design — Dialog controls | https://learn.microsoft.com/en-us/windows/apps/design/controls/dialogs-and-flyouts/dialogs | Dialogs block the current window and need a safe nondestructive choice; contextual validation belongs inline. |

## Accessibility and keyboard behavior

| Official source | Direct URL | Program use |
| --- | --- | --- |
| W3C ARIA APG — Modal dialog pattern | https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/ | On open, move focus into the dialog; contain Tab/Shift+Tab; support Escape when safe; on close, restore focus. For irreversible actions, initial focus belongs on the least destructive choice. |
| WCAG 2.2 Understanding 4.1.3 — Status Messages | https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html | Announce success, progress, and errors without forcing focus; use appropriate live-region semantics and avoid noisy token-by-token announcements. |
| WCAG 2.2 Understanding 2.4.7 — Focus Visible | https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html | Every keyboard-operable control needs a visible focus indicator that is not hidden by sticky UI. |
| WCAG 2.2 Understanding 1.4.10 — Reflow | https://www.w3.org/WAI/WCAG22/Understanding/reflow.html | Preserve content and functionality when zoomed or narrowed; the browser test target must still reflow to 320 CSS px even though the native app can enforce a larger practical minimum. |

## Tauri desktop security, filesystem, and packaging

| Official source | Direct URL | Program use |
| --- | --- | --- |
| Tauri v2 — Capabilities | https://v2.tauri.app/security/capabilities/ | Capabilities constrain which permissions apply to named windows/webviews. Future windows must receive separate least-privilege capability files; overlapping capabilities merge authority. Registered custom commands must be explicitly restricted before adding new windows. |
| Tauri v2 — Content Security Policy | https://v2.tauri.app/security/csp/ | Keep CSP restrictive, bundled, and free of remote scripts. Expand `connect-src` only for explicitly approved local endpoints; never relax script policy for convenience. |
| Tauri v2 — Embedding external binaries | https://v2.tauri.app/develop/sidecar/ | The Python Core is a valid sidecar pattern; target-triple packaging and explicit shell scopes are part of release proof. Frontend code must not gain arbitrary process execution. |
| Tauri v2 — Dialog plugin | https://v2.tauri.app/plugin/dialog/ | Use native open/save/folder pickers for explicit filesystem approval and native message/confirmation dialogs when appropriate. Current ZANA does not include this plugin, so the frontend cannot invent file-picking behavior. |
| Tauri v2 — File System plugin | https://v2.tauri.app/plugin/file-system/ | File access is scope-based and traversal constrained. ZANA should still keep document/archive bytes and validation in Core; the frontend should pass only picker-approved paths through typed commands. |
| Tauri v2 — Window State plugin | https://v2.tauri.app/plugin/window-state/ | If restoration is approved, persist only window geometry/state through the native plugin and clamp restored bounds to available displays. Current ZANA does not include it. |
| Tauri v2 — Updater plugin | https://v2.tauri.app/plugin/updater/ | Update artifacts require signature verification that cannot be disabled. Updating is a separate, explicit product decision and must not create hidden telemetry or mandatory cloud dependence. |

## Local AI and model-management contracts

| Official source | Direct URL | Program use |
| --- | --- | --- |
| Ollama API — List models | https://docs.ollama.com/api/tags | Render only models returned by the runtime; preserve digest, size, family, format, quantization, and modified metadata only when present. |
| Ollama API — Show model details | https://docs.ollama.com/api-reference/show-model-details | Detail enrichment comes from the runtime response; unknown fields remain unknown and raw metadata must be bounded/redacted. |
| Ollama API — Pull a model | https://docs.ollama.com/api/pull | Pull is a runtime-native POST and can stream progress. ZANA must never proxy weights, claim completion before post-pull discovery confirms identity, or fabricate totals when the runtime omits them. |
| LM Studio — OpenAI compatibility endpoints | https://lmstudio.ai/docs/developer/openai-compat | `/v1/models` is an interoperability surface, not proof of vendor identity. Manual endpoints and provider-specific enrichment remain explicit. |

## Streaming, server state, and offline behavior

| Official source | Direct URL | Program use |
| --- | --- | --- |
| WHATWG HTML — Server-sent events | https://html.spec.whatwg.org/multipage/server-sent-events.html | Respect event ids and reconnect semantics, but keep ZANA's persisted job snapshot authoritative. |
| MDN — `EventSource()` constructor | https://developer.mozilla.org/en-US/docs/Web/API/EventSource/EventSource | The constructor exposes URL and `withCredentials`, not arbitrary request headers. Because ZANA auth is header-only, native `EventSource` is not an acceptable transport for the current API. |
| MDN — Using readable streams | https://developer.mozilla.org/en-US/docs/Web/API/Streams_API/Using_readable_streams | Authenticated `fetch` can consume `Response.body` incrementally and can be cancelled with `AbortController`; implement a bounded parser and distinguish disconnect from Core job cancellation. |
| TanStack Query — Network mode | https://tanstack.com/query/latest/docs/framework/react/guides/network-mode | Represent fetching and paused/offline state separately. Do not report cached data as newly confirmed or use browser online status as evidence that a local runtime is healthy. |
| TanStack Query — Query cancellation | https://tanstack.com/query/latest/docs/framework/react/guides/query-cancellation | Consume the supplied `AbortSignal` for superseded reads. Aborting a read must not cancel the underlying durable Core job. |
| SQLite — Write-Ahead Logging | https://www.sqlite.org/wal.html | WAL allows readers and one writer to progress concurrently on the same host, but checkpointing and WAL/SHM files matter for backup/export. Never copy only the database file while Core is live and call it a coherent backup. |

## Observability and artifact standards

| Official source | Direct URL | Program use |
| --- | --- | --- |
| OpenTelemetry — Logs | https://opentelemetry.io/docs/concepts/signals/logs/ | Model local operational records as timestamped structured events with severity and correlation context. ZANA's current observability remains local/redacted; this reference does not authorize remote telemetry. |
| OCI Image Specification | https://github.com/opencontainers/image-spec | ZANA Image export/import uses standard content-addressed OCI layout semantics and digest verification; the desktop must not present a proprietary package fiction. |

## Synthesis decisions

1. The desktop is a two-level operator workspace: stable grouped primary navigation, then screen-local tabs/list-detail. Deep page trees and marketing dashboard cards are rejected.
2. Query lifecycle, Core connectivity, runtime availability, durable job lifecycle, and user permission are independent axes. The UI must not collapse them into one spinner or one red/green badge.
3. Core owns filesystem bytes, digests, parsing, persistence, leases, jobs, and truth. Tauri owns native process/window/dialog/credential boundaries. React owns presentation and bounded ephemeral interaction state.
4. The current authenticated job-event endpoint requires a header-bearing fetch-stream client and page reconciliation, not native `EventSource`, a token query parameter, or blind polling.
5. Native dialogs and scoped capabilities are prerequisites for production file selection/export/import. Browser file APIs or unrestricted frontend filesystem permission are not substitutes.
6. Destructive confirmation is proportional: inline undo for reversible view state; modal confirmation for deleting registrations, resetting memory, or switching image state; typed-name/digest confirmation only when irreversibility and blast radius justify it.
7. Determinate progress requires a trustworthy numerator/denominator or Core fraction. Otherwise show the named phase, elapsed time, last durable event, and an honest indeterminate treatment.
8. Telemetry remains off. Local observability can be rich without becoming remote analytics.

## Citation review

- Every claim above is a paraphrase or a ZANA-specific inference; no competitor expression is copied.
- Each URL points directly to the official standards owner, framework owner, platform vendor, or runtime vendor.
- The implementation prompt requires a fresh access-date check because APIs and platform guidance can change after this package snapshot.
