# Task usage and cost reporting

`make telemetry-up` applies the independent harness schema, including migration 003.
It preserves historical rows and the shared telemetry volume. No application migration
or application API changes are involved.

Normal work uses automatic machine collection through Make. `start-task` binds the actual
client session, captures its source counters and posts the starting statistics comment to
the selected issue. Subsequent lifecycle events capture every registered session;
`create-pr` posts the second comment with baseline/latest counters and their difference.
These two checkpoints are required for Codex, Claude and Kimi. They are separate from the
final issue total: review, merge and the current unfinished response can add usage later.

## Source setup and lifecycle

| Client | Authoritative machine source | Session binding |
|---|---|---|
| Codex | `CODEX_HOME/sessions/**/*.jsonl`, default `~/.codex/sessions`; `token_count.info.total_token_usage` | `CODEX_THREAD_ID` (or explicit session ID) |
| Claude | `CLAUDE_CONFIG_DIR/projects/*/<session>.jsonl`, default `~/.claude`; finalized `message.usage` | `CLAUDE_SESSION_ID`, explicit ID, or matching ancestor in Claude's process registry |
| Kimi Code 2.4.x | `KIMI_CODE_HOME/sessions/*/session_<id>/agents/main/wire.jsonl`, default `~/.kimi-code`; protocol 1.5 `usage.record` | `KIMI_SESSION_ID` or explicit ID; no selection by newest file |

Client identity comes from `BSS_TASK_OWNER`, independently of session, provider/model and
worker slot. Codex/Claude runtime detection is also supported. Kimi's installed Rust client
stores data under `.kimi-code`, not the Python CLI's `.kimi` directory. Its wire protocol
differs from the Python CLI's `StatusUpdate` format: unsupported formats are explicitly
unavailable. Do not infer usage from `token_counting.measured`, context size or rate limits.

Where the runtime does not expose its session ID, pass its actual machine session identity
on startup or handoff. This requires session metadata, not a prepared token export:

```sh
make start-task BSS_TASK_OWNER=kimi BSS_TELEMETRY_SESSION_ID=<actual-session-uuid>
make telemetry-collect CLIENT=kimi SESSION_ID=<actual-session-uuid>
make telemetry-collect
```

The first command runs from primary, the others from the selected task worktree. On every
resume/new session/client handoff run `telemetry-collect` before implementing. A bound
session is remembered in PostgreSQL, so subsequent Make operations capture it without
repeating the identity. Missing identity creates an explicit `session_identity_missing`
collection row rather than guessing a nearby session. Its gap remains visible even after
another session is bound; investigate the missing session instead of deleting its evidence.

The binding starts at the selected client's startup event, or at handoff registration for
later sessions. Codex cumulative counters are converted to adjacent deltas across the
whole source before filtering the bound interval. Repeated cumulative events are ignored;
counter regressions mark coverage partial. Claude sums uncached input, cache reads and
cache creation into inclusive input, and collapses finalized content-block copies by
message ID. Streaming placeholders are excluded. Claude's source does not establish the
billing provider: it remains `unknown` unless the machine record supplies it. Kimi uses
each request's observed token breakdown, not an aggregate context counter. Machine model
changes are preserved per sample. No missing count is filled with a guessed zero.

Supported delegated sessions are discovered through Codex machine parent-thread metadata,
Claude's session-owned `subagents/agent-*.jsonl`, and Kimi's session-owned agent directories.
Each has its own source/session identity and inherited issue boundary; nested Codex
delegation is followed. A source without a supported relationship must be registered
explicitly; the collector never attributes unrelated sources by recency or worker slot.

Rebinding a thread to another issue closes the previous window. A later binding caps the
previous interval so an event cannot belong to both issues. Finalization waits for a machine
turn end after closure, since a Make command executes before the enclosing agent response
has finished. `finish-task` (including externally merged PRs) closes all registered windows,
prints every client's status, and starts a bounded two-minute final-tail collector from
surviving primary code. It can finish after the task worktree has been removed. Missing
final counters remain `partial / final_counter_pending`; they never become fabricated zeroes.

```sh
make telemetry-collect TASK=issue-124-example
make telemetry-watch TASK=issue-124-example
make telemetry-comment PHASE=start
make telemetry-comment PHASE=pr
make telemetry-report
```

Explicit `TASK=` recovery works from primary after cleanup. `telemetry-watch` retries for
up to two minutes; sources and bindings remain available for another retry. `CLOSE=1` on
`telemetry-collect` ends work on all registered sessions when handing off a completed task
outside the normal finish lifecycle. Do not close a session that is still doing issue work.
The ordinary Make lifecycle remains fail-open with bounded warnings on storage, source or
GitHub failures. Explicit collection/posting commands return a failure for unavailable
collection or publication. Restore storage with `make telemetry-up`, then retry. When
storage was unavailable at initial binding, supply the original session explicitly; its
unobserved earlier work cannot be declared complete automatically.

The starting comment is immutable across retries. If reconstructed later, it uses the
machine counter at the binding boundary, not the current counter relabelled as a baseline.
The PR checkpoint updates its existing comment on retry; only comments by the authenticated
GitHub user with the exact task/checkpoint marker are eligible. Other authors' comments are
never edited. A failed/uncertain POST is recovered by finding that marker before posting
again. Comments contain only validated usage metadata; prompt/response, raw source records,
credentials and arbitrary environment/command output are never stored or published.

`collection_complete` requires all expected clients and bound sessions to have a confirmed
final interval with known counters. `missing_clients`, `missing_sessions` and per-session status/reason show
coverage gaps independently of `cost_complete`. Work outside the Make/session-binding
contract, unsupported delegation and upstream source omissions cannot be proven complete;
do not describe the report as a provider invoice or a guarantee about unregistered work.
Historical samples and bindings survive finish cleanup. Manual alternate sources for an
already imported client/session are refused to prevent cross-source double counting.

There is no bundled live rate card. Set `BSS_TELEMETRY_PRICING_FILE` before automatic
collection, or pass `PRICING_FILE=` to explicit collection, to price new matching samples
with an auditable versioned card. Existing prices are immutable, including unknown prices;
automatic retries preserve them. Claude's cache-creation pricing requires an additional
rate absent from the legacy three-rate formula, so automatic Claude costs stay unknown.
Unknown providers/models, unsupported costs and subscription charges remain unknown.

`make telemetry-probe CLIENT=<client> SESSION_ID=<actual-id>` validates a real source
without assigning historical usage to the current issue. It reports content-free counts,
source identity and a SHA-256 of the allowlisted projection only. Real local evidence for
all three clients is in [telemetry-usage-evidence.md](telemetry-usage-evidence.md).

## Explicit interchange and pricing

An external exporter can still import an explicit, content-free machine usage delta:

```sh
make telemetry-ingest USAGE_FILE=/absolute/path/usage.json
make telemetry-ingest USAGE_FILE=/absolute/path/usage.json PRICING_FILE=/absolute/path/pricing.json
make telemetry-report
```

The interchange importer never scans conversation logs, reads provider credentials, calls
billing APIs or estimates tokens from text. The automatic collector projects machine usage
metadata only; an external exporter can supply an equivalent usage record directly.
The source identifier should identify that export format/version. The importer validates
structure and consistency, not the authenticity of an external provider's export.
Do not enter an agent's estimate as an export. There is no bundled live price table.

The version-1 interchange record contains only these fields (example identities and counts
below are synthetic):

```json
{
  "version": 1,
  "task_id": "issue-92-example",
  "sample_id": "request-1",
  "provider": "example",
  "client": "example-client",
  "model": "example-model",
  "session_id": "session-1",
  "source": "provider-export-v1",
  "sampled_at": "2026-10-31T12:00:00Z",
  "kind": "delta",
  "input_tokens": 1000,
  "cached_input_tokens": 200,
  "output_tokens": 50,
  "total_tokens": 1050
}
```

The task must already exist in telemetry, as it normally does after startup. Session,
provider and client identities are required, bounded identifiers. Model and all token
counts may be omitted or null; omitted counts stay null. Timestamps require a timezone.
Input counts **include** cached input. A supplied total must agree with supplied input
and output counts. The importer never invents a missing total, model or zero cache count.
`kind: "unavailable"` explicitly records a source with no usable usage; all counts must
then be null. Unavailable providers use the same command and schema.

Each delta must represent a distinct nonoverlapping interval or request. Cumulative
session counters must be normalized by a trustworthy exporter before import; cumulative
records are rejected. Reusing `(task, provider, client, session_id, source, sample_id)`
with identical values and pricing is a no-op. Changed counts, model, timestamp, kind or
pricing under that identity fail the entire transaction. Do not rename duplicate samples
to circumvent this check, or import the same usage through two sources. Imports are
serialized by a PostgreSQL transaction lock, including concurrent retries.

Workflow sessions and imported sessions have separate namespaces; usage is associated
with a task by its validated task ID. Imported samples retain the raw external session ID
and provider/client/model snapshot even if the model changes during a session. Old rows
are preserved without backfilling invented provenance.
Legacy costs without import/pricing provenance are excluded from priced subtotals.

An optional pricing record has exactly the following fields. These rates are synthetic
test data, not prices for a real model:

```json
{
  "version": "fixture-v1",
  "source_url": "https://example.com/pricing",
  "effective_from": "2026-10-01T00:00:00Z",
  "effective_until": "2026-11-01T00:00:00Z",
  "provider": "example",
  "model": "example-model",
  "currency": "USD",
  "input_per_million": "2",
  "cached_input_per_million": "0.5",
  "output_per_million": "8"
}
```

Use an auditable published source and a version for the applicable rate card. Preserve
the provider's effective dates; the sample must fall in the half-open validity period.
Provider and model must match exactly. Rates are nonnegative decimal strings per million
tokens. The complete pricing record is stored with each sample. Changing rates/version
affects new samples only; existing samples cannot silently be repriced. The importer
does not fetch a URL or establish that a supplied price is accurate.

Cost is `(input - cached) * input_rate + cached * cache_rate + output * output_rate`,
divided by one million, rounded to ten decimal places using decimal half-even rounding.
All three counts must be observed; missing counts leave cost null. Total tokens alone
cannot establish cost. Currency totals remain separate. These are rate-card estimates,
not invoices: subscriptions, negotiated discounts, non-token charges and provider-specific
tier adjustments are outside this calculation. Use a matching rate card only for usage
to which this formula applies.

The JSON report includes every task's observed tokens, known cost subtotal per currency,
sample count and pricing completeness. `cost_complete` means every **recorded sample**
has a price, not that every provider request was captured. Token totals are null when
there are no samples or any sample lacks that count. Even a complete subtotal cannot
prove the task's total spend: omitted sessions/requests cannot be detected. A task is
marked merged after a successful merge event or confirmed completion by `finish-task`.
Use that field to select merged tasks for cost/token analysis.

Timings measure startup to the first successful PR creation, observed green CI, and
successful merge event. Missing observations stay null, including human actions outside
the lifecycle helpers. Validation failures/retries count `make check` result events;
retries mean checks after the first. CI failure counts are **readiness observations**,
not distinct CI runs: repeated polling can count the same failure repeatedly. `ci_retries`
is null because the event stream does not retain reliable CI run/attempt identities.
Completed-task groups count each task once per observed provider/client/model and slot;
groups describe participation and must not be added together as disjoint totals. Unknown
identities remain null. Slot association comes from task events, not inferred model identity.

Only allowlisted fields reach storage. Unknown keys (including prompts, responses,
environment data and caller-supplied costs) fail validation. JSON duplicate keys and
oversized exports are rejected. No raw input files, command arguments or database errors
are retained. Errors print a fixed, content-free message. Explicit ingestion/report
commands return nonzero on failure so callers can retry; normal lifecycle telemetry
retains its existing fail-open behavior and never depends on a usage import.

`make runtime-check` covers validation, machine adapters, missing/partial counts, privacy,
session windows/delegates, final-tail retries, the two issue comments, pricing versions,
failure handling and fixture reports without Docker. `make telemetry-test` checks the
actual PostgreSQL schema, transactional duplicate imports and exact numeric cost using
rolled-back test rows.
