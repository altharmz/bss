# Task usage telemetry

Machine usage (tokens per client, model and session) is recorded per task in a local
PostgreSQL database for later analysis. It is observational and **fail-open**: a telemetry
failure prints a warning and never changes a workflow's exit status. Unknown values stay
null; nothing is guessed or reported as zero. No prompt, response, code or secret is
stored - only allowlisted fields.

## Storage

`compose.telemetry.yaml` is a separate `bss-telemetry` project with the volume
`bss-telemetry-data`; task `down`, `clean` and `finish-task` never touch it.

```
make telemetry-up        # start the database and apply the numbered SQL migrations
make telemetry-health    # check database and schema
make telemetry-migrate   # apply new migrations (scripts/telemetry/*.sql, immutable once applied)
make telemetry-down      # stop, keep the volume
make telemetry-test      # schema test in a rolled-back transaction
```

Tables: `tasks` (issue branch, start/end/outcome), `agent_sessions`, `workflow_events`
(operation, result, duration, bounded metadata) and `usage_samples` (token counts; cost
columns exist but nothing fills them automatically). Rows are kept indefinitely.

## Collecting usage

`make start-task` binds the client session; later lifecycle targets collect automatically.
On resume or hand-off run `make telemetry-collect`. Kimi agents always pass the session:
`make telemetry-collect CLIENT=kimi SESSION_ID=<uuid>` (if automatic discovery fails at
startup, `BSS_TELEMETRY_SESSION_ID=<uuid> make start-task BSS_TASK_OWNER=kimi`).

| Client | Machine source | Session binding |
|---|---|---|
| Codex | `~/.codex/sessions/**/*.jsonl` (`token_count`) | `CODEX_THREAD_ID` |
| Claude | `~/.claude/projects/*/<session>.jsonl` (`message.usage`) | process registry or `CLAUDE_SESSION_ID` |
| Kimi Code | `~/.kimi-code/sessions/*/session_<id>/agents/main/wire.jsonl` | session-lock registry or `KIMI_SESSION_ID` |

Delegated sessions are followed where the client records the parent relationship; an
unrelated source is never attributed by recency or slot. A rebinding closes the previous
window, so one event never belongs to two issues. `finish-task` closes all windows and
starts a bounded two-minute final-tail collector. A missing final counter leaves the
collection `partial / final_counter_pending`.

Other commands: `make telemetry-watch TASK=<task>` (retry the final tail),
`make telemetry-probe CLIENT= SESSION_ID=` (validate a source without attributing it) and
`CLOSE=1` on `telemetry-collect` to end all sessions of a task finished outside the lifecycle.

## Reporting

`make telemetry-report` prints JSON per task: observed tokens, sample counts, coverage
(`collection_complete`, `missing_clients`, `missing_sessions`), startup-to-PR/CI/merge
timings, and groups by provider, client, model and slot. Token totals are null when there are
no samples or any sample lacks a count. Groups describe participation and must not be added
together. Coverage cannot prove that unregistered work is absent; this is not an invoice.
