# Task usage and cost reporting

`make telemetry-up` applies the independent harness schema, including migration 002.
It preserves historical rows and the shared telemetry volume. No application migration
or application API changes are involved.

Import an explicit, content-free machine usage export:

```sh
make telemetry-ingest USAGE_FILE=/absolute/path/usage.json
make telemetry-ingest USAGE_FILE=/absolute/path/usage.json PRICING_FILE=/absolute/path/pricing.json
make telemetry-report
```

The importer never scans conversation logs, reads provider credentials, calls billing
APIs or estimates tokens from text. Current lifecycle processes expose a thread identity,
but do not expose a reliable provider usage counter to this collector. Their usage stays
unknown. A client/exporter must supply a record from its machine-readable usage source;
the source identifier should identify that export format/version. The collector validates
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

`make runtime-check` covers validation, missing/partial counts, privacy, pricing versions,
failure handling and fixture reports without Docker. `make telemetry-test` checks the
actual PostgreSQL schema, transactional duplicate imports and exact numeric cost using
rolled-back test rows.
