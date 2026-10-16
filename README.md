# CredUsage

A passive macOS menu bar app for viewing AI allowance readings in one place. Each reading shows its source, account-binding status, observation time, reset window, and freshness. Unavailable integrations remain visible with an explanation and a dashboard action.

CredUsage is an independent project. It does not change CredClaude, CredCodex, their settings, or their runtime data. It does not submit prompts, refresh credentials, purchase credits, or switch accounts.

## Menu bar and usage details

Click the monochrome CredUsage emblem to open a native popover for **Antigravity, Codex, Claude Code, and Cursor**. Rows use official provider artwork, percent used, the actual usage window, and a reset countdown. Five-hour windows are preferred when the source reports one. The menu bar stays icon-only. Overview and details share a fixed **250 × 325 pt** window with native macOS material, neutral progress bars and highlights, borderless action buttons, and standard pickers. All content scales proportionally by 125% from the compact layout while preserving the 10:13 ratio. The popover follows the system appearance.

Click a provider for a compact detail panel with **no scrolling**. Codex and Claude show compact short-term and long-term tiles with percent used, reported window duration, reset countdowns, and calculated percentage left. Exact local reset times and source facts are available in tooltips and accessibility descriptions. Context and quota-pool selectors keep every reported pool separate; credits appear only for their associated pool. The status strip shows refresh time, freshness, and account verification or reported credit balance.

Cursor and Antigravity show provider-specific source readiness and numerical-source limitations, with no invented usage bars. Every detail panel offers **Refresh** and its existing usage/dashboard link; demo mode disables external links. Unknown usage has no fabricated percentage, and stale/expired/undated observations retain their labels until new source evidence arrives. Unsupported lifetime and token statistics are omitted.

Use Tab or the arrow keys to move through rows, Return to open details, Left Arrow to go back, and Escape or an outside click to dismiss. **Settings** opens a separate window containing connection setup, source actions, refresh, preferred-context selection, notifications, startup, logs, diagnostics, and About. ChatGPT and Gemini remain configurable there; their allowances are separate from the coding products.

Native screenshots use **synthetic demonstration readings**, not live account data: [overview](docs/images/usage-popover-dark.png) and [provider details](docs/images/usage-details-dark.png). Light and dark appearances use native macOS styling.

## Run locally

Requires an **arm64 Mac**, macOS 12 or later, and Python 3.11+ for development. This build was tested on macOS 27.0.1; earlier supported versions have not been exercised. A built app contains its own Python runtime.

```bash
cd /Users/veerr_89/Work/tools/cred
python3.11 -m venv .venv
.venv/bin/python -m pip install --cache-dir .cache/pip -r requirements-build.txt
.venv/bin/python -m credusage --demo
```

Demo mode uses isolated app-owned data and disables real network and login actions. Start normal monitoring with `.venv/bin/python -m credusage`. Vendor tools retain ownership of authentication. Normal app state lives in `~/.credusage/`; no old app data is migrated.

```bash
.venv/bin/python -m pytest
.venv/bin/python -m black --check credusage tests
.venv/bin/python -m isort --check-only credusage tests
.venv/bin/python -m credusage --self-check
.venv/bin/python -m credusage --help
.venv/bin/python -m credusage --once
.venv/bin/python -m credusage --diagnostics /tmp/credusage-diagnostics.json
./build_app.sh
```

`--once` performs passive collection; it does not run inference. Diagnostic exports redact sensitive keys and paths, but review an export before sharing it.

## Provider support

| Product | Available behavior | Numerical evidence gate |
| --- | --- | --- |
| Codex | Documented app-server protocol; quota windows and push-triggered reads | Fixture-tested; passive smoke on Codex 0.159.2 returned three Plus buckets (original-display comparison pending). A quota `accountId` alone cannot prove the individual user. Unverified observations are labeled and cannot trigger quota alerts or reuse account-specific disk snapshots. |
| Claude Code | Explicit passive status-line ingestion | Fixture-tested. Requires user-supplied telemetry; no Keychain or OAuth-token collection. Missing event timestamps remain undated. |
| Antigravity | Installation/version discovery and setup actions | CLI 1.2.15+ required. Actual `/usage` JSON schema is not verified; numerical collection remains unavailable. |
| Cursor | Installation discovery and dashboard action | Personal/free account source not verified; dashboard-only. |
| ChatGPT consumer | Separate product and dashboard action | No verified passive numerical source. Codex allowances are not reused. |
| Gemini consumer | Separate product and dashboard action | No verified passive numerical source. Antigravity allowances are not reused. |

See [provider evidence and limitations](docs/PROVIDER_SUPPORT.md). Live verification is tracked independently from fixture tests in [verification](docs/VERIFICATION.md).

## Claude status-line ingestion

List configured connections, then ingest a passive payload obtained from Claude Code's existing status-line callback (replace `<UUID>` with the Claude connection ID):

```bash
.venv/bin/credusage --list-connections
.venv/bin/credusage ingest-claude-statusline --connection "<UUID>" < statusline.json
```

This command only reads bounded stdin and retains the necessary usage, session, and version fields. It does not modify Claude settings. A real status-line callback can supply current quota data; a static pasted example does not prove fresh usage. [Setup instructions](docs/CLAUDE_STATUSLINE.md) explain how to preserve an existing status-line command.

## Build and install

The supplied CRED artwork is bundled as a transparent macOS app icon. Every build regenerates all native and Retina icon sizes from `credusage/assets/AppIcon.png`; run `./build_icon.sh` to regenerate only the icon.

```bash
./build_app.sh
./install.sh
```

Quit CredUsage before installing an update or uninstalling; both scripts refuse while a CredUsage process is active. The installer builds first, then installs only `CredUsage.app` to `~/Applications`. It neither launches nor registers login startup by default. Optional flags are explicit:

```bash
./install.sh --launch
./install.sh --login
./install.sh --destination /Applications
./uninstall.sh
./uninstall.sh --remove-data
```

Uninstall retains `~/.credusage/` unless `--remove-data` is supplied. Build output is `dist/CredUsage.app`, with a self-contained archive at `dist/CredUsage-macOS-arm64.zip`. Unzip and drag the app into Applications to install it manually. The app lives in the menu bar, so opening it does not show a Dock window. The bundled command is `dist/CredUsage.app/Contents/MacOS/CredUsage` and accepts the same CLI arguments, including ingestion. Distribution is subject to the [license and attribution notes](THIRD_PARTY_NOTICES.md). Developer ID signing and notarization are separate release gates.

Run the built app without installing:

```bash
open dist/CredUsage.app
# Isolated demo instead:
open dist/CredUsage.app --args --demo
# Headless startup and packaged-resource check:
dist/CredUsage.app/Contents/MacOS/CredUsage --self-check
```

Black uses a 100-character line length; isort uses the Black profile. To format changes locally, run `.venv/bin/python -m isort credusage tests` then `.venv/bin/python -m black credusage tests`.

Read [architecture](docs/ARCHITECTURE.md), [adding a connector](docs/EXTENDING.md), [reuse decisions](docs/REUSE.md), and [implementation decisions](docs/DECISIONS.md).
