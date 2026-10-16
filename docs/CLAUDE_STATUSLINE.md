# Passive Claude Code status-line setup

CredUsage does not edit Claude settings and does not read Keychain or OAuth tokens. The adapter accepts the documented JSON supplied to a Claude Code status-line callback. The [vendor documentation](https://code.claude.com/docs/en/statusline#rate-limit-usage) defines the fields; missing usage remains unknown and missing event time remains undated.

Run `credusage --list-connections` to obtain the configured Claude connection UUID, or inspect it in preferences. This initializes app-owned configuration on the first run. Replace `<UUID>` below with that ID. A manual ingestion looks like:

```bash
/path/to/cred/.venv/bin/credusage ingest-claude-statusline --connection "<UUID>" < statusline.json
```

For a bundled installation use:

```bash
"$HOME/Applications/CredUsage.app/Contents/MacOS/CredUsage" ingest-claude-statusline --connection "<UUID>" < statusline.json
```

To integrate an existing callback, make an app-owned wrapper that reads the incoming JSON once, passes the same original JSON to the existing status-line command, and separately pipes it to the ingestion command. Preserve the existing command's stdout and exit behavior. Keep ingestion output on stderr or discard it so it cannot alter the visible status line. Do not replace the existing callback with an ingestion-only command; ingestion is not a status-line renderer.

The ingestion command retains only required rate-limit, session, and version metadata, then atomically writes normalized app-owned telemetry. It reads bounded stdin, rejects malformed JSON, and does not retain prompt text, transcript bodies, tool arguments, paths, or credentials. It rejects missing/disabled/non-Claude connections and associates data with the current connection generation. Disabling/reconnecting the connection prevents an old telemetry file from being accepted.

A repeated payload without a source timestamp remains undated. Its local receipt time is not a current observation claim. Manually pasted or fixture payloads do not qualify as live verification. Quota notifications also require a verified individual identity binding; a Claude session ID is not proof of that identity.

For a live verification, allow an ordinary existing Claude Code callback to run, then compare the original product's display against CredUsage for the same session, window, units, and reset time. This verification must not initiate an inference request just to populate the monitor.
