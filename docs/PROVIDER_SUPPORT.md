# Provider evidence and support

Evidence date: October 16, 2026. “Implemented” describes code paths. “Fixture-tested” describes deterministic contract tests. “Live-verified” requires a separate same-account comparison against the original product, with matching scope, units, and window. The presence of an installed app or successful CLI process is not a numerical verification.

| Integration ID | Policy / source | Current support | Unresolved gate |
| --- | --- | --- | --- |
| `openai.codex` | Documented local app-server stdio protocol | Initialization, request routing, account checks, rate-limit reads, notifications, bounded shutdown; fixture-tested | A quota `accountId` is tenant evidence, not an individual subject. Active cache reuse and quota alerts remain disabled unless individual binding evidence is verified. Passive smoke on CLI 0.159.2 returned Plus plan and three buckets; same-account original-display comparison remains pending. |
| `anthropic.claude` | Local passive status-line telemetry | Explicit bounded ingestion, version/session/usage filtering, documented window parsing; fixture-tested | User must supply callback telemetry. Missing event time stays undated. No credentials are collected; live callback comparison pending. |
| `google.antigravity` | Documented standalone CLI discovery | `agy` version gate and setup/status actions; actual usage parser withheld | CLI 1.2.15+ and actual sanitized JSON required before a source-specific schema can be implemented and verified. |
| `cursor.editor` | Dashboard-only | Installation discovery and dashboard action | No verified passive personal/free-account quota source. Enterprise APIs do not establish free-account access. |
| `openai.chatgpt` | Dashboard-only consumer integration | Separate registry entry and dashboard action | No verified passive numerical source. Codex observations are not consumer ChatGPT limits. |
| `google.gemini_app` | Dashboard-only consumer integration | Separate registry entry and dashboard action | No verified passive numerical source. Antigravity observations are not consumer Gemini limits. |
| Example connector | Production-disabled demo | Multiple windows, qualitative availability, shared-pool evidence, incomplete data and failure demonstrations | Never used as a real provider or alert source. |

## Codex

The [official app-server contract](https://learn.chatgpt.com/docs/app-server) defines initialization and request/notification exchange. The adapter prefers `rateLimitsByLimitId`; a legacy projection is used only when the map is unavailable. Percentages are not rescaled. Identity-less push notifications enqueue a new account/usage read and are not committed directly to account-bound storage. No auth file is copied and no renewal is attempted.

## Claude Code

The [status-line usage contract](https://code.claude.com/docs/en/statusline#rate-limit-usage) documents `rate_limits` with five-hour/seven-day percentages and reset timestamps. Telemetry is local and user supplied. Receipt time does not replace a missing source observation time. The [credential-use guidance](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use) is the reason the reference Keychain/OAuth collector is excluded.

## Antigravity

The [official changelog](https://antigravity.google/docs/changelog?tab=cli) establishes passive structured `/usage` output for the standalone CLI. Version 1.2.15 is the minimum gate. The app does not launch an older command or submit `/usage` to an interactive prompt. An unverified schema remains unavailable even when experimental opt-in is enabled.

## Cursor and consumer products

[Cursor's API documentation](https://cursor.com/docs/api) describes enterprise capabilities; it does not prove a personal/free account usage endpoint. Cursor remains dashboard-only until the actual account's passive source is validated. [Gemini usage guidance](https://support.google.com/gemini/answer/16275805?hl=en) describes product limits, not a supported machine-readable collection API. ChatGPT and Gemini are distinct integrations with explicit source limitations.

Undocumented collectors require per-connection opt-in plus verified passive behavior, units, and identity handling. Opt-in alone never promotes an unknown schema, user identity, or quota semantics to verified.
