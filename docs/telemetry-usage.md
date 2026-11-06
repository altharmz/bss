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

Only a real checkout writes: scripts refuse storage when `compose.telemetry.yaml` is missing
(a disposable test repository) or `BSS_TELEMETRY_DISABLED=1` (set by `make runtime-check`
for every suite). Before this guard, harness tests wrote rows for fixture tasks; migration
`004_test_rows.sql` removed them.

Tables: `tasks` (issue branch, start/end/outcome), `agent_sessions`, `workflow_events`
(operation, result, duration, bounded metadata), `usage_samples` (token counts; one machine
sample belongs to at most one task), `usage_collections` (one bound window per task and
session, `binding` explicit or delegated), `model_prices` and `task_facts`. Rows are kept
indefinitely.

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

**Subagents bind their own issue.** For Claude, the PreToolUse hook
`.claude/hooks/agent-telemetry.py` sees `agent_id` (present only on a subagent's tool call)
and inserts `BSS_TELEMETRY_AGENT=<id>` after each `make` that starts a command segment
(never inside quotes or a heredoc). `make start-task` run by a subagent therefore binds
`<session>:agent:<id>`, not the orchestrating parent. Codex subagents have their own thread
id already.

A delegated child (found through the parent relationship the client records) is added to
the parent's task only when it started inside the parent's window and never bound a task
itself; an orchestrator running several issues in parallel would otherwise charge every
child to whichever issue it bound last. An unrelated source is never attributed by recency
or slot. An explicit rebinding closes the previous window; a sample already stored for one
task is skipped by any other. `finish-task` closes all windows and
starts a bounded two-minute final-tail collector. A missing final counter leaves the
collection `partial / final_counter_pending`.

Other commands: `make telemetry-watch TASK=<task>` (retry the final tail),
`make telemetry-probe CLIENT= SESSION_ID=` (validate a source without attributing it) and
`CLOSE=1` on `telemetry-collect` to end all sessions of a task finished outside the lifecycle.
`RESET=1` (with `TASK=`, usually with `CLIENT= SESSION_ID=`) drops that task's bindings and
samples and re-attributes it from the source files, which are never changed. The prepaid
epic (#183-#194) was re-attributed this way, one subagent per issue.

## Reporting

`make telemetry-report` prints JSON per task: observed tokens, sample counts, coverage
(`collection_complete`, `missing_clients`, `missing_sessions`), startup-to-PR/CI/merge
timings, and groups by provider, client, model and slot. Token totals are null when there are
no samples or any sample lacks a count. Groups describe participation and must not be added
together. Coverage cannot prove that unregistered work is absent; this is not an invoice.

**Cost** is computed at report time from `model_prices`, loaded from
`scripts/telemetry/prices.json` by `make telemetry-prices` (also on `telemetry-up` and
`telemetry-migrate`). It is the API-equivalent list price, not what a subscription is billed.
`input_tokens` is inclusive, so uncached input = input - cache reads - cache writes; cache
writes (`cache_creation_tokens`) are priced separately. A sample with an unknown model,
date or cache-write count is unpriced: `cost_complete` is false and `unpriced_models` names
it. Codex reports no cache writes and counts them as zero; Codex and Kimi models have no
prices yet.
