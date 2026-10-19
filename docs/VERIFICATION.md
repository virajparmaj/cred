# Verification record

## Narrower windows and larger provider logos — October 17, 2026

Applied the user's width and icon refinements: overview/details are 256 pt wide (20% narrower), and Settings is approximately 597 pt wide instead of 746 pt, with horizontal control positions/widths reduced proportionally. Provider logos doubled from 22 to 44 pt in overview rows and 28 to 56 pt in details. Progress tracks and content columns are shorter; base details are now 392 pt tall to fit the larger logo and wrapping source text. Settings/Quit use icon-only 32 pt buttons with accessible names and tooltips. The user confirmed that “remove service selection” means the blue row outline: rows remain clickable, hover remains subtle, and keyboard focus uses a small neutral leading cue. Context/quota selectors are preserved.

All **211 tests passed** against the rebuilt app; formatting and whitespace checks passed. Native smoke passed for light/dark views, narrower geometry, doubled logos, icon-only footer labels/tooltips, all provider pages, selectors, action feedback, unknown/zero usage, stale/undated/expired readings, keyboard navigation and dismissal, and Settings save/refresh. Synthetic captures were visually inspected and refreshed under `docs/images/`. Full spoken VoiceOver navigation remains manual.

The running app quit gracefully, the bundle was rebuilt, plist/signature/resource checks and ZIP integrity passed, and the updated `dist/CredUsage.app` was reopened and confirmed running. ZIP size: **15,099,393 bytes**. Executable SHA-256: `1a7be7b3cc3caf894d544f12932c46f127482c54da86db0c2982991349533f71`. Evidence: `.qa/narrow-native-smoke.log`, `.qa/narrow-popover/native-smoke.json`, `.qa/narrow-tests.log`, `.qa/narrow-build.log`, and `.qa/narrow-relaunch.json`.

## Supplied redesign — October 17, 2026

Implemented the supplied point-based design: 320 × 320 pt overview, 320 × 352 pt base detail view, 32 pt extensions for each selector and action-feedback row, and a 24 pt extension for the separate demo entry. Quotas and metadata are unboxed, typography uses native system point sizes, and unknown usage has no progress track. The overview age updates without requiring new source data and represents the oldest successful timestamp among visible numerical readings.

All **211 tests passed** after rebuilding, including the 11 packaging tests. Black, isort, and diff whitespace checks passed. Native WindowServer smoke passed with synthetic data in light and dark appearances: four provider pages, standard/extended geometry, context and pool selection, selection preservation, long labels, exact credit accessibility values, zero versus unknown usage, missing windows, stale/expired/undated readings, source labels alongside action feedback, demo external-action guards, keyboard navigation, dismissal and separate Settings save/refresh. Native captures were visually inspected; light-mode progress fills were corrected to resolve the intended appearance. Full spoken VoiceOver navigation remains manual.

The rebuilt arm64 app passed plist validation, strict deep ad-hoc signature verification, bundled-resource self-check and ZIP integrity checks. ZIP size: **15,099,127 bytes**. Executable SHA-256: `480381ffe411dfd4e4b664431823fd84cb242e266c9f7e4c9c700fe6ee0e2691`. This pass did not install or restart the normal monitoring app, or establish new live-provider verification. Settings implementation, provider collection and stored data were preserved.

Evidence: `.qa/redesign-popover/native-smoke.json`, `.qa/redesign-native-smoke.log`, `.qa/redesign-tests.log`, and `.qa/redesign-build.log`. Updated native screenshots are in `docs/images/`. Records below describe preceding builds.

Latest Antigravity integration update (October 16, 2026): parsed the user's actual CLI 1.4.0 `command.data.groups[].buckets[]` payload, preserving remaining fractions, four quota buckets, reset times, and separate Gemini/Claude-GPT model groups. All 209 automated tests pass, including 22 new Antigravity cases; Black/isort and diff whitespace checks pass. Source and rebuilt-app passive collection both returned connected health with all four buckets. The 11 packaging tests passed against the rebuilt bundle. Native smoke passed with synthetic Antigravity cards, model-group switching and selection preservation after refresh; the captured layout was visually inspected. The running menu-bar app was gracefully restarted with the new bundle; its normal collection log recorded Antigravity `code=OK` at 19:09:41 America/Chicago. Desktop automation timed out attaching to the live app, so the final live-menu inspection was not completed; native source UI smoke passed separately. Evidence: `.qa/live-antigravity/verification.json`, `.qa/live-antigravity/relaunch.json`, and `.qa/compact-popover/native-smoke.json`. The latest ZIP is 15,094,824 bytes; executable SHA-256 is `68d76f908fd17d6b187ec235dd173e1c46f4f30bdce3f15541e7415a036a59f9`. Account identity and an independent original-display comparison remain unverified. The size/build records below describe preceding releases.

Historical proportional-size update: the window is 250 × 325 pt, rendering the 200 × 260 logical layout at 1.25×. Native smoke verifies the physical frame and content size, unchanged logical control positions, light/dark captures, all provider pages, selectors, keyboard navigation, and dismissal. All 187 tests and formatting checks pass against the rebuilt app; ZIP integrity, strict signature verification, and bundled resource self-check pass. Build evidence: `.qa/scaled-build.log`; native evidence: `.qa/scaled-native-smoke.log`; relaunch evidence: `.qa/scaled-relaunch.json`. The ZIP is 15,091,765 bytes. Executable SHA-256: `bd2be050811b35b84c524ba796f022caa6d1c290ea9f83e01cc19674137fdb31`. The earlier size/build records below document the preceding compact release.

Evidence is scoped to the operation actually checked. Fixture success does not establish live provider correctness, and a relocatable command-line smoke does not establish native visual quality.

## Commands

Run from the new repository:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m black --check credusage tests
.venv/bin/python -m isort --check-only credusage tests
.venv/bin/python -m credusage --self-check
.venv/bin/python -m credusage --help
.venv/bin/python -m credusage --once --demo
# Native interaction/screenshot smoke; requires WindowServer access:
.venv/bin/python tests/native_popover_smoke.py
./build_app.sh
/usr/bin/codesign --verify --deep --strict dist/CredUsage.app
/usr/bin/plutil -lint dist/CredUsage.app/Contents/Info.plist
```

Build environment: arm64 macOS 27.0.1, project-local Python 3.11.14, PyInstaller 6.22.3, rumps 0.4.0, PyObjC 12.2.2. Dependencies are installed only in this project's `.venv`; no runtime dependency on either reference checkout exists.

## Prior build evidence

| Area | Status |
| --- | --- |
| New independent `.venv` and declared build dependencies | Created and installed on October 16, 2026. |
| App and menu icons | User-supplied CRED app artwork and interlocking menu silhouette. Native/Retina app sizes plus an 18pt 1x/2x menu template are bundled. Official actual Codex light/dark icons, orange Claude sunburst, Cursor and Antigravity app assets are included locally; no vendor app is required to render them. |
| Automated suite | 187 passed against the rebuilt artifact, including fixed geometry, compact-detail grouping/action coverage, and exact-versus-rounded credit display. Coverage includes separate Codex pools and credits, pool IDs containing colons, missing/different windows, undated Claude telemetry, fractional percentages, overages, unknown values, context selection, reset expiry, and quick-action guards/failure feedback. Black and isort checks pass. Fixture tests do not establish live-provider correctness. |
| arm64 onedir `.app` | Fixed 200 × 260 pt layout build passed: about 27 MiB, arm64 Mach-O, Python/runtime/assets included; resource self-check, plist lint and strict deep ad-hoc signature verification passed. Self-contained ZIP is 15,092,510 bytes and passes archive integrity checks. |
| Relocation/minimal environment | Latest build passed in a different path containing spaces with `PATH=/usr/bin:/bin`: self-check confirms frozen arm64 runtime, bundled assets, native dependencies and platform-independent core. Relocated `--once --demo` with an isolated data directory returned five synthetic observations. Evidence: `.qa/half-size-bundle-smoke.json`. Packaging tests also verify relocation under an isolated HOME. |
| Native runtime linkage | Passed: all 65 bundled Mach-O libraries inspected; absolute dependencies are limited to Apple `/System` and `/usr/lib`. No Homebrew, source checkout or virtualenv runtime linkage. |
| Packaging tests | 11 passed, including relocation, signature, native identity, asset integrity, linkage and side-effect-free shell help/syntax checks. Install/login actions were not invoked. |
| Native source demo GUI | Earlier bounded shell smoke verified main-thread timers, real demo collection, and Settings save. Current compact view interaction coverage is recorded below; full packaged pointer/VoiceOver workflows remain separate checks. |
| Native compact interactions | Saved smoke `tests/native_popover_smoke.py` passed: native material captured in light and dark appearances; Overview and all four provider-specific pages remain exactly 200 × 260 pt with no scroll views; the four overview rows, paired quota tiles, metadata strip, source line, action row, and footer fit the panel; progress colors are neutral; fractional percentage text fits its metric tile; the rounded credit label keeps its exact source value in accessibility metadata; context/pool selection and preservation after refresh; long labels; a stale selector retaining identity after context reordering; mocked Refresh/dashboard actions and demo external-action guard; native Tab/Return/Escape; outside-click dismissal; Back/reopen; accessibility labels; separate Settings save/refresh and preservation of an unreported saved pin. Animations are disabled only in the synchronous harness. Evidence: `.qa/compact-popover/native-smoke.json`; screenshots are in `docs/images/`. Full spoken VoiceOver navigation remains manual. |
| Frozen native GUI / relaunch | Previous running instance quit gracefully and was confirmed absent before replacement. Latest half-size bundle finished launching from this checkout and remained running afterward (PID 42983); normal passive Codex collection logged `OK`. Evidence: `.qa/half-size-relaunch.json`. A packaged pointer walkthrough was not performed; desktop automation previously timed out attaching to the app. Native interaction verification above tests the same source implementation. |
| Codex passive source smoke | Codex CLI 0.159.2 returned Plus plan and three quota buckets using `account/read` and `account/rateLimits/read` only; individual subject remains unverified. The final application controller also accepted three buckets with connected health and sanitized tool version. No inference submitted. Evidence: `.qa/live-codex/verification.json`. |
| Codex same-account original-display comparison | Pending; passive source success alone is not a live-verified claim. |
| Claude bundled fixture ingestion | Passed UUID discovery, bounded ingestion, filtered unknown sensitive fields and passive readback; 0.5 remains 0.5%, event time remains null, identity remains unverified. |
| Claude actual callback comparison | Pending; no user telemetry supplied. |
| Antigravity | CLI 1.4.0 installed and authenticated; real user output retained as an allowlisted fixture. Source and rebuilt-app passive reads accepted all four quota buckets with connected health. Native group-selection smoke passed; identity and independent original-display comparison remain unverified. |
| Cursor free-account numerical source | Unverified; dashboard-only. |
| Consumer ChatGPT/Gemini numerical sources | Unavailable; separately labeled. |
| Developer ID signing / notarization | Not performed; release gates. |

Historical half-size executable SHA-256 (`dist/CredUsage.app/Contents/MacOS/CredUsage`): `2161fd9993b3063ab4b7593dbcd00f36c79aaed6c258ee92c3bf591b08b7eca9`. This identifies the half-size tiled build with 200 × 260 pt geometry. All 187 tests, including the 11 packaging checks, passed against the rebuilt artifact; ZIP integrity and relocated isolated demo collection passed. Ad-hoc signatures are not Developer ID notarization.

Codex and Claude source tool versions are retained as sanitized numeric versions in observation provenance and redacted diagnostics. Raw Codex user-agent text and arbitrary telemetry version strings are not exported as tool versions.

## Manual acceptance

Launch the built demo app, inspect long connection labels, multiple windows, qualitative values, stale/expired/undated rows, preferences and diagnostics. Check that slow collection leaves menus responsive, a second instance exits, and disabling/reconnecting rejects late results. Demo must disable network/login actions and use isolated storage. Close the app after inspection.

For bundle relocation, copy `dist/CredUsage.app` under a different path containing spaces, run its CLI with a minimal environment (`PATH=/usr/bin:/bin`), and confirm resources load without the checkout or its virtualenv. No install/login/uninstall operations should run during automated tests. Check installer shell syntax, destination safeguards and explicit launch/login flags separately. Both scripts refuse to replace/remove a running CredUsage process; installer rollback restores the previous bundle if interrupted between directory moves.

For live providers, use passive reads only, compare the same account, scope, units and window against the original display, and record date/version/evidence. If a tool, telemetry, identity, or real payload is unavailable, retain that connector's source gate and report it explicitly.
