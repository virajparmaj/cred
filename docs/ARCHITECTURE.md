# Architecture

CredUsage is a Python 3.11+ application with a platform-independent core and a native macOS menu shell. The working name is CredUsage and the bundle identifier is `com.veer.credusage`.

## Boundaries and data flow

1. A static registry provides integration descriptors and constructed built-in connectors. A descriptor declares policy, capabilities, minimum refresh interval, and validation evidence; product names do not imply quota support.
2. Discovery uses explicit executable/application checks. It never logs in or submits prompts.
3. One coordinator schedules connector `collect()` calls on four workers. Each connection has one active operation, while shared-source gates govern related connections. UI callbacks enqueue triggers and render cached presentation data.
4. Connectors return typed results with generation, process epoch, binding evidence, completeness, removals, observations, and redacted diagnostics.
5. The coordinator rejects obsolete results, applies source cooldowns and backoff, and publishes immutable state. Persistence and notification work run outside the state lock. A main-thread native timer consumes the latest state and renders menus; worker callbacks never invoke Cocoa.

Contracts live in `credusage/domain/contracts.py`; records and enums live in `credusage/domain/models.py`. These layers do not import Cocoa, access credentials, or perform I/O. Injectable clocks, process runners, typed results, storage, and notification callbacks keep failure paths testable. HTTP and credential-access protocols are small boundaries for future collectors; current sources do not extract credentials.

## Independent state dimensions

Connection health describes transport/auth/source readiness. Freshness describes each observation as fresh, stale, expired, or undated. Observation basis distinguishes reported data, telemetry, estimates, and demo data. Validation status describes implementation evidence (fixture-tested or live-verified). One dimension never substitutes for another.

`IdentityBinding` distinguishes subject, account, organization, workspace, and project. A verified binding requires individual subject evidence. Email, plan, and tenant/account ID alone are insufficient. Fresh unverified observations can be shown as an unverified local context; they cannot restore account-specific history or trigger quota alerts. A generation change invalidates in-flight requests. Source process epochs reject late results from a replaced process.

## Numerical and merge contracts

Percentages are percentages: 0.5 means 0.5 percent, not 50 percent. Money and credits use `Decimal` with lossless JSON representation. Boolean and non-finite inputs are rejected; legitimate overages remain visible. Currency code and credit unit are part of quantity compatibility. Shared pools require explicit evidence and never result from a matching label alone.

Complete results replace the connection's complete bucket set. Partial results update only supplied compatible buckets; explicit removal keys delete named buckets. Merging requires a verified matching identity, generation, and process epoch. A changed metric or unit is rejected. Each replacement retains its own complete quantity, currency, credit-unit, window, and provenance records; fields from separate samples are never combined. Reset countdown expiry marks an old observation expired. It does not create a zero-usage observation or claim restored availability.

## Scheduling and storage

Eligible numerical sources default to five-minute polling, subject to their minimum interval. Startup, manual refresh, menu open, wake, and push notifications coalesce through the same scheduler. Manual refresh cannot bypass source throttles or `Retry-After`. Workers have bounded source deadlines; connector shutdown terminates owned processes within a bounded interval.

Schema-versioned JSON is written atomically with private permissions under `~/.credusage/`. Original observation times survive restarts. Corrupt state is preserved for recovery while defaults remain usable. No credential migration, history database, cloud service, or inference endpoint exists. Demo state is isolated from normal state.

`refresh_state.json` persists UTC source cooldowns. Server throttles survive manual refresh and restart; local authentication backoff can be retried explicitly after completing vendor login. Launching login clears the active observation, increments its generation, and displays pending state until a new read succeeds.

## UI and alerts

The popover uses real point dimensions with no coordinate scaling: 256 × 320 pt for the overview and 256 × 392 pt for base details. Each required context/pool picker adds 32 pt, and action feedback adds a separate 32 pt row. A synthetic demo entry adds 24 pt to the overview. The menu-bar icon retains its existing size. Settings narrows from 746 to approximately 597 pt by scaling horizontal control positions and widths to 80%, keeping vertical dimensions and native text rendering intact.

An AppKit status item shows only the template emblem and opens a transient native popover. `ui/dashboard.py` builds immutable overview, context, and limit records without Cocoa or I/O. The overview has four coding-product rows; details use the same popover and keep contexts separate. An actual five-hour window is preferred, then the shortest non-credit window. The pinned connection selects a preferred context; existing pin fields remain compatible, but a bucket no longer drives a menu-bar text title. `ui/presentation.py` retains the existing generic textual presentation helpers.

`ui/popover.py` owns native controls and navigation; `ui/compact_details.py` renders provider details without `NSScrollView`. `ui/detail_layout.py` supplies point-based geometry and quota groups. Headers/footers are 40 pt high, outer padding is 16 pt, and four 56 pt overview rows align 44 pt icons, provider names, trailing percentages and reset labels. Unknown usage omits the progress track. The overview's relative update label uses the oldest successful timestamp among enabled, displayed numerical readings; a tooltip explains that meaning. Native popover material, automatic appearance, arrow, shadow and framing remain system-owned. Row backgrounds respond only to hover or press. Keyboard focus uses a small neutral leading mark, with no blue selected-row outline. Settings and Quit are 32 pt icon-only buttons, retaining accessible labels and tooltips. Progress fills resolve the semantic label color under the intended appearance before being retained, avoiding light/dark transition mismatches.

Antigravity/Codex/Claude details have a 56 pt provider logo and two unboxed 100 pt columns at x16 and x140. Five-hour/seven-day durations display as “5-hour”/“Weekly”; other durations retain their actual value. Usage percentages start at 26 pt with an 11 pt “used” suffix, shrinking only when necessary to fit; redundant percentage-remaining labels are omitted. Historical limits retain their per-window freshness labels and faded progress fills. Plain metadata shows update/check time, freshness, and reported credits or account verification. Source/verification stays visible above equal-width Refresh and provider-page actions, including when a separate feedback row is present. Exact reset timestamps and credit quantities remain in tooltips and accessibility descriptions. Cursor retains dashboard-only readiness; unavailable panels use the same layout without fabricated metrics.

Codex primary/secondary/credit readings group by adapter bucket identity (splitting only the final slot suffix, preserving colons inside pool IDs); grouping does not claim shared allowances. Antigravity groups retain provider group names and bucket IDs. Native selectors carry represented IDs so stale callbacks cannot select another context merely because its sort position changed. Source/verification can wrap to two lines at the narrower width. Each optional selector gets a full-width row and shifts the quota/metadata/actions/footer downward without compressing them. Selection survives refresh. Refresh and existing provider dashboard actions use the normal controller/action runner; sign-in and configuration stay in Settings. Presentation geometry is not persisted, so no configuration or snapshot migration is needed.

AppKit views update on the main thread only when presentation, selection, geometry, or appearance changes. Countdown and freshness changes use cached observations; `ResetRefreshTracker` requests each newly expired reported window once through the normal scheduler without manufacturing a refill. Native preferences open in a separate window and own configuration, refresh, pin/context selection, provider actions, About, logs, reset, and diagnostics. Demo retains its synthetic integration identity, never represents fixture readings as real provider usage, and blocks external dashboard actions.

Quota alerts default to 80 and 95 percent used, with five-point hysteresis. Durable deduplication keys include identity, bucket, and window instance (or rolling episode). Alerts require verified identity and a fresh, dated real observation. Demo/fixture data and expired or undated readings cannot notify; time passing alone cannot rearm a recovered allowance.
