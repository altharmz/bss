# Prepaid release validation (P7)

Scope: prepaid slices P1-P6c2 (migrations `0034`-`0047`, TMF654 runtime, MCP tools, UI).
Nothing here was deployed by this slice; `make deployed` says what is on the VPS.
This record does not verify the VPS image or schema.

## Mixed-mode demo seed

`make seed` (tests/seed.py) now creates two prepaid accounts beside the seven
postpaid ones, all through the public API and the worker's prepaid passes
(`produce_renewals`, `process_commands`, `observe_entitlements` at 11:00 each simulated day):

| Account | Story |
|---|---|
| Robert Taylor (prepaid) | top-up, immediate purchase, funded renewals, one lapse for lack of funds, a top-up that resumes, a final top-up; active and funded today |
| Linda Harris (prepaid) | top-up, purchase, runs out at the first renewal; product suspended, renewal pending with a needs-top-up reason |

`tests/test_seed.py` pins both stories and the unchanged postpaid data. An existing local
database seeded before P7 holds 7 of 9 accounts and is reported as a partial seed:
`make clean && make up && make seed`.

## Validation evidence

`make check` (2026-10-24): `ruff` clean; 1,970 backend tests passed in the CI profile
(389.85 s); `docs-check` matched; 319 frontend tests passed in each timezone (UTC and
America/New_York). `make web-e2e` on the rebuilt, freshly seeded stack passed all five
Chromium smoke tests. No test was changed except the seed test's counts (9 accounts, daily
runs counted by kind, prepaid command runs written at 11:00).

## Recovery and rollback

The recovery runbook and the downgrade barrier (`0034` onward refuse retained prepaid
history) are in [operations.md](operations.md#prepaid-recovery-and-rollback). A schema
rollback after prepaid use is not supported; recover forward.

## Browser check

2026-10-25: `make web-e2e` on the seeded stack passed all six Chromium smoke tests,
including the new prepaid test ([slice 7](next-slices-design.md#7-prepaid-browser-check-and-release-evidence)):
Robert Taylor's card (Ledger, Available and Paid-through KPIs, three or more Balance action
rows, one detail), a 10.00 cash top-up (toast, Ledger changes), Add product starting today
with the prepaid preview and the accepted order (the worker does not run inside the test,
so only the accepted text is asserted), Linda Harris's funding-suspended product with the
needs-top-up text and the Top up button, and the Prepaid commands list. The test leaves
the data changed; the suite runs against a disposable seeded stack.

## Not covered

- Deployment, and the VPS run of `make smoke`/`mcp-smoke` against prepaid.
