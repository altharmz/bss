# Local machine usage evidence (#124)

On 2026-11-02 the Make-owned `telemetry-probe` command parsed real local sources for
all three clients. These are read-only checks of pre-existing sessions, not synthetic
usage assigned to issue #124. Only the allowlisted machine usage projection was hashed;
no prompts, responses, raw records, credentials or source paths are included here.

| Client/source | Observed samples | Input (inclusive) | Cached input | Output | Total |
|---|---:|---:|---:|---:|---:|
| Codex / codex-rollout-v1 | 49 | 4,457,087 | 4,348,288 | 37,336 | 4,494,423 |
| Claude / claude-request-v1 | 16 | 950,080 | 905,274 | 10,876 | 960,956 |
| Kimi Code 2.4.1 / kimi-wire-1.5 | 184 | 21,603,682 | 21,111,959 | 73,328 | 21,677,010 |

Content-free projection SHA-256 values:

- Codex: `ac42cf18a85c75d96811d4ee8f8a7a87e1e3805858ce460c379ef8df84ed2b30`
- Claude: `8c948c8a28822e6fc0948b80d5f2a7bf8257693f25905691f100d8e864370da4`
- Kimi: `0a4db790f2b33f5b47393d50c13f3e674aead5f5a2f53b1ff27ce65e0621ddf2`

Codex was still active (no observed turn end); its totals can grow, so a subsequent probe
has a different digest. Claude exposed two final turn markers and Kimi twenty-four. All
three probes returned no parser limitation. Claude repeats finalized usage across content
blocks with different envelope timestamps; the adapter retains one stable request copy.
The installed Kimi source uses protocol 1.5 `usage.record` and camelCase token fields,
not Python CLI `StatusUpdate` records. Billing-provider attribution for Claude and any
unsupported pricing remain unknown. The probes do not establish complete issue coverage.

For the actual implementation issue, `make telemetry-collect` also ingested the current
Codex session into persistent storage with `partial / session_open` coverage, correctly
separating an active session from a completed issue. The source/session IDs remain in the
local machine ledger and the issue's required statistics comments. Rerun a probe using
those identities; use explicit `TASK=` collection only for a session actually bound to
that issue. Historical Claude/Kimi sources were not imported as this issue's work.
