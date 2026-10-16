# Adding a connector

Implement the `UsageConnector` protocol: `discover(DiscoveryContext)`, `collect(CollectRequest)`, `actions(Connection)`, and `close()`. Keep provider parsing outside presentation and scheduling. Register an `IntegrationDescriptor` with stable integration/vendor IDs, supported auth modes, capabilities, source policy, minimum interval, and evidence.

1. Establish a passive documented source or require explicit per-connection experimental opt-in. Do not submit prompts or reuse another product's limits. Obtain a sanitized real payload before defining an undocumented parser.
2. Specify the source contract: exact numeric units, null/missing semantics, timestamp meaning, window identity, account scope, currency/credit units, and shared-pool evidence. Use strict normalization and `Decimal`.
3. Respect request generation, deadline, and process epoch. Return typed results with completeness and explicit removals. Distinguish authentication, transport, throttling, schema, and unsupported-source failures through safe diagnostic codes.
4. Supply `IdentityBinding` only from reliable evidence. `account_id` without an individual subject does not authorize account-specific history or alerts. Never infer subject identity from email and plan equality.
5. Inject process/transport/clock dependencies. Add sanitized fixtures and tests for malformed/non-finite values, overage, multiple windows, missing fields, expired timestamps, identity changes, late results, throttles, timeout, and shutdown.
6. Add only allowlisted action URLs/argv. Vendor tools own login and renewal; a credential reference is not permission to extract a token.
7. Update the support matrix. Record fixture tests separately from live verification. The latter requires same-account, same-scope, same-unit, same-window comparisons without inference.

Shared-source throttles and generic presentation already exist; do not create a second timer, worker pool, or source-specific menu tree. New UI capabilities should be added to generic presentation models.
