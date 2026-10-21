# Subscriber-number release validation (N6)

Scope: N1–N5 subscriber numbers, independently of prepaid runtime. N4
(`47b7525`, migrations through `0033`) was confirmed deployed by the human;
N5 UI (`91297f6`) is not deployed yet. This record does
not independently verify the VPS image or schema.

## Negative proofs

Both temporary mutations were reverted; `app/ordering/numbers.py` matches its
committed version.

| Mutation | Test | Observed failure |
|---|---|---|
| Bypass the administrative lock check | `test_unavailable_numbers_reject_without_products[locked]` | The order completed instead of being rejected. |
| Pass zero quarantine days to the availability query | `test_quarantine_starts_at_effective_release[3]` | Reuse on the effective release date completed instead of being rejected. |

Logs: `/tmp/bss-n6-negative-lock.log` and
`/tmp/bss-n6-negative-quarantine.log`.

## Validation evidence

Full CI and dev backend runs each passed all 1,495 tests. Logs:
`/tmp/bss-n6-test-2.log` (CI, 269.26 s) and `/tmp/bss-n6-test-3.log`
(dev, 289.04 s). The progress collector's complete suite run also passed all
1,495 tests (896.56 s), supplying the third successful release pass and
generated coverage evidence. Its two existing warnings concern assertion
rewriting of already-imported `anyio` and the `cache_dir` option with the cache
plugin disabled.

The initial dev backend run passed 1,494 tests and failed the existing
`test_a_scheduled_run_keeps_the_worker_alive` timing assertion: its first gap
was 1.00244 seconds against a 1-second threshold while frontend checks were
also running. Remaining full passes run without concurrent frontend checks.
The timing threshold and application behavior have not been changed.

Backend/frontend lint and `make docs-check` passed. All 284 frontend tests
passed in each configured timezone (UTC and America/New_York).
`make web-e2e` built the production UI and passed all five Chromium smoke
tests, including number inventory navigation and create-form validation;
disposable sign-in users were cleaned up successfully.

Progress collection also passed all 284 frontend tests. The first render exposed
five scope paths using `{id}` instead of the implemented `{hub_id}` or
`{resource_id}`. Corrected those five labels in `scope.yaml` and the cached scope,
verified the entire cached scope equals the YAML source and all collected evidence
is unchanged, then reran the standard renderer successfully. No collector, harness
or runtime code changed. The report has no unscoped items: 43/43 database items,
160/188 API operations, 35/35 UI items and 15/15 worker items are implemented and
tested. The remaining 28 API operations are planned TMF654 runtime. All subscriber
number items are implemented and tested; deployment remains unknown in the report.

The backend suite validates TMF639 responses against the pinned contract through
`TmfClient`, resource events against listener schemas, contract provenance and
the gap ledger. It also covers order-owned assignments, future reservations,
concurrent claims, cancellation conflicts, both quarantine settings, immutable
requirements after order history, accepted-claim grandfathering, unchecked legacy
resource documents, and migration preservation/lossy-downgrade refusal. UI request
bodies are pinned in component tests and replayed against the backend.

## Operational boundaries and approval

- `MSISDN_QUARANTINE_DAYS` defaults to zero. An increase applies to new eligibility
  checks; accepted claims remain valid. Locks prevent new assignments and preserve
  accepted future assignments and current service.
- The picker offers currently available unlocked inventory. Orders perform the
  authoritative interval check, including future claims and quarantine. Refresh
  inventory to see later availability.
- After an uncertain UI write, inspect inventory or product/order history before
  retrying. Number creation, locking and number-change dialogs prevent blind retries
  after an uncertain result.
- Numbers and their retained assignment history cannot be deleted together through
  the API. Use orders for number changes and release; preserve the audit history.
- There are no new migrations in N5/N6. Existing `0032`/`0033` downgrade barriers
  refuse retained inventory/history or requiring products/number-change orders.
  An older image does not reverse schema changes; do not erase history to make a
  downgrade succeed.
- Prepaid account creation remains unavailable; TMF654 runtime is a later milestone.

Commit, push and deploy follow the normal cycle (no approval). On deployment, inspect
deployment smoke
results and confirm `~/bss/DEPLOYED`; leave that open until actual VPS evidence
exists. No deployment is performed by this validation slice.
