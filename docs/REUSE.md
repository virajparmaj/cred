# Reference review and reuse

This repository is independent of the CredClaude and CredCodex projects. Those projects are read-only references; no installer, build, runtime path, or data migration targets either project.

| Reference pattern | Decision |
| --- | --- |
| Native rumps menu and main-thread callbacks | Adapt the interaction pattern; present generic domain rows. |
| Native settings, URL launching, icon lookup, single-instance behavior | Adapt patterns with app-specific identity and app-owned paths. |
| Fixed Claude/Codex quota models | Replace with typed provider-neutral buckets, windows, provenance, identity, and freshness. |
| Synchronous menu collection | Replace with one coordinator and four bounded collection workers. |
| Fraction/percentage guessing | Reject; each adapter defines units explicitly. |
| Stale snapshots hidden after failure | Replace with visible historical data and independent refresh-health state. |
| Manual refresh bypassing throttle | Reject; all triggers pass the same source gates. |
| Credential/Keychain extraction and renewal | Exclude. Vendor tools retain credential ownership. |
| Repo/venv-dependent app launcher | Replace with a PyInstaller onedir app containing Python and dependencies. |
| Reference runtime state and icons | Do not migrate. Create independent state and original CredUsage artwork. |

The reviewed reference baselines were 88 Codex tests and 216 Claude tests during planning. Their passing count does not validate this app; CredUsage has separate tests and live verification gates.

CredClaude carries an MIT license, reproduced in `THIRD_PARTY_NOTICES.md`. CredCodex had no license metadata in the inspected checkout. No external redistribution permission is inferred from access to its source. Review any substantial copied material and resolve its license before external distribution. New code and original icon assets in this project require the owner's chosen project license before public redistribution.
