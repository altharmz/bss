# Documentation findings

What writing these docs turned up (2026-10-09, branch `cloud/docs`). Nothing here was
fixed in code: each item is for the human to decide. "Code" is what the docs describe;
where CLAUDE.md or another doc says something else, the docs follow the code.

## Code and documentation disagree

1. **Fixed 2026-10-09** (the commit that adds `make check`): both scripts call
   `scripts/billing-status-check.sh`, which accepts 200 `ok|running`; its cases run in
   `make test`. Original finding: **`/billing-status` can answer 200 `running`, but the
   deploy and the local smoke reject it.** `app/status.py` answers 200 `{"status": "running"}` for a run younger
   than the 2 h grace (proven by `tests/test_billing_status.py::test_the_latest_run_decides`),
   while `scripts/deploy.sh` (lines 251-255) and `scripts/smoke.sh` accept a 200 only
   with `"status": "ok"`. CLAUDE.md ("Deployment (slice f)", now in [decisions/worker.md](../decisions/worker.md)) states the same "200 must say
   ok" rule as the scripts. Consequence: a deploy (or `make smoke`) while a billing run is
   in progress - e.g. right after the deploy itself restarted the worker mid-run, which
   is then taken over - fails at the smoke step although the app is healthy. Fix would be
   one alternative in two `case` lines (`ok|running` for 200); not done here
   (`scripts/` needs the human's OK).
2. **Fixed 2026-10-09** (same commit): `docs/cloud.md` now says push works, only to
   `cloud/*` branches named on the command line. Original finding: **`docs/cloud.md`
   says `git push` is denied in a cloud session** ("`git push` is
   denied by settings.json and blocked by the guard"). Since `1c9f98e` it is not:
   `.claude/settings.json` allows `git push`, `git push origin:*` and `git push -u
   origin:*`, CLAUDE.md allows plain pushes, and this session pushed `cloud/docs` with
   `git push -u origin cloud/docs`. `docs/cloud.md` ("The harness in a cloud session")
   is stale.
3. **Fixed 2026-10-10** (branch `cloud/catalog-integrity`): POST requires the one related
   party to carry `role: "customer"` exactly (missing or other: 422, nothing stored), so
   the role answered is the stored relation (`app/api/account.py`, `CUSTOMER_ROLE`;
   `tests/test_api_parties.py::test_billing_account_party_role_must_be_customer`).
   Original finding: **`billingAccount.relatedParty[].role` is not checked.** The TMF666 mapping ([decisions/tmf-api.md](../decisions/tmf-api.md))
   says the related party is "role `customer`"; the code (`app/api/account.py`,
   `RelatedParty.role: str | None`) accepts any role, or none, on POST and always answers
   `role: "customer"`. No test sends another role.
4. **Fixed 2026-10-09** (same commit): "Stage order", the TMF678 note and the status-flip
   notes in CLAUDE.md describe the running worker. Original finding: **CLAUDE.md still
   describes stage 4 as future in several places** although slice f is
   deployed: "Stage order" says the billing core is "done, not wired" and that bill and
   ABR lists "stay empty until the stage 4 worker calls `bill_account`"; the TMF637
   section's "Known: a product ordered with a future start date stays `created` until the
   stage 4 worker activates it" and the `pendingTerminate` note describe what the worker
   now does (`app/worker/process.py`, `flip_statuses`). The docs describe the current
   behaviour.
5. **CLAUDE.md calls the frontend planned.** "Domain model & decisions" (now
   [decisions/data-model.md](../decisions/data-model.md)) says "`frontend/`
   is planned (stage 5)"; it is built and deployed (`frontend/`, 10 of 13 UI items in
   PROGRESS.md; first web deploy `9424b9b` per CLAUDE.md itself).
6. **No `/backup-status`.** The task brief for these docs mentions "backups and
   /backup-status"; no such endpoint, script or check exists in this repository. Backups
   are vps-infra's ([decisions/infra.md](../decisions/infra.md#shared-infrastructure-vps-infra)); operations.md says so.

## Behaviour that is not tested (or not guarded)

7. **Fixed 2026-10-10** (branch `cloud/catalog-integrity`): specifications and prices
   follow the offering's forward-only lifecycle (409 otherwise;
   `tests/test_api_catalog_lifecycle.py`). Customer `status` stays ours
   (`Created`/`Active`/`Inactive`: TMF629's is a free string with no enum to take values
   from); a PATCH with any other value is a 422, now locked by
   `tests/test_api_parties.py::test_customer_status_outside_the_enum_is_refused_on_patch`.
   Customer transitions stay free. Original finding: **Lifecycle of specifications,
   prices and customers is unguarded.** Only the
   offering's `lifecycleStatus` is restricted to forward steps; PATCH may set a
   specification's or a price's `lifecycleStatus`, and a customer's `status`, to any value
   (`app/api/catalog.py`, `app/api/customer.py`). Whether that is intended is not written
   down; no test covers it.
8. **Fixed 2026-10-10** (branch `cloud/catalog-integrity`): PATCH may not move the start
   into the past, nor set or move the end into the past (409;
   `tests/test_api_offering_validity.py`). Ending an offering needs no price-gap check:
   billing never reads its `validFor`, so its live products stay priced
   (`::test_ending_an_offering_leaves_its_live_products_priced`). Original finding:
   **An offering's `validFor` can be changed freely by PATCH** (`app/api/catalog.py`,
   `patch_offering`): moved into the past, or ended before live products' dates. It only
   affects new orders (`offeringNotValid`), not billing, but no rule or test covers it.
9. **Fixed 2026-10-11** (branch `cloud/quality-1`): a product's `creationDate` is the
   creating order's `order_date`, an ABR's `date` the new `applied_at` (migration 0007:
   the clock of the run's attempt that wrote it, back-filled from the run's
   `started_at`); `tests/test_clock_dates.py`, and `tests/test_seed.py` checks the seed's
   history carries the simulated instants. Original finding: **`creationDate` (TMF637
   product) and `date` (TMF678 ABR) come from the database's `now()`**, not the injected
   clock - the mapping in [decisions/tmf-api.md](../decisions/tmf-api.md) says so, but it means
   seeded demo data shows the seed's run time there, not the FixedClock dates the seed
   simulates. Every other business instant is from the clock.
10. **Bills never leave `state: new`** and nothing settles a bill against payments; the
    balance is account-level only. TMF678's other states are in the enum but unused.
11. **Product statuses `pendingActive`, `suspended` and `aborted` are never set** by any
    code path (the enum is TMF's); the worker's flip handles `pendingActive` anyway.

## Unclear areas and environment notes

- **Fixed 2026-10-10** (branch `cloud/catalog-integrity`): a takeover sets `started_at`
  to the takeover time, so the 2 h grace counts from it; a running run of a date before
  the expected date is 503 regardless, so a worker that keeps dying cannot hold 200
  forever (`tests/test_billing_status.py::test_the_grace_of_a_taken_over_run_counts_from_the_takeover`,
  `::test_a_run_taken_over_again_and_again_is_overdue_once_its_date_is_late`). Original
  note: **`billing_run.started_at` on a takeover** is kept (`app/repository/billing.py`,
  `take_over_run`), while a reopen after a failure resets it. So `/billing-status`
  measures a taken-over run's 2 h grace from the original start. Not stated in CLAUDE.md;
  the column comment of migration 0005 says it.

12. **The spec files' server URLs differ from our base paths** (TMF629's spec says
    `/tmf-api/customer/v5/`, TMF676's `basePath` is `/payment/v4/`, others have no
    `/tmf-api` prefix). The paths here are a project decision ([decisions/tmf-api.md](../decisions/tmf-api.md), "TMF API
    conventions"); the generated API pages show both.
13. **FastAPI's `/docs`, `/redoc`, `/openapi.json` are served** (not disabled in
    `app/main.py`) - behind basic auth in production. Open decision (CLAUDE.md, "Next
    session", item 8).
14. **The docs generator reads list filters from an error message.** FastAPI's OpenAPI
    does not know the TMF filters (handlers read query parameters themselves), so
    `tests/docs_collect.py` sends an unknown parameter and parses
    "unknown query parameter X (filters: ...)" from `app/api/listing.py`. A change of that
    message makes the generated pages say "filters: none" for every list - visible in the
    drift check, but silent otherwise.
15. **`scripts/cloud-setup.sh`: a pip download hung for over 10 minutes** in this
    session (the process sat in `poll` on a socket); killing it and running the same
    install with `pip install --timeout 30` finished in seconds. A `--timeout` (and
    possibly `--retries`) in the script would make a stalled download fail fast.
16. **Cloud-only verification of `make docs`.** The collector ran here in the venv
    against host PostgreSQL 16, not through `docker compose run app`; the `docs` and
    `docs-check` targets themselves have not been run. The drift check's shell condition
    was proven both ways by hand (a changed file and an untracked file each reported).

## Found later (open)

Added 2026-10-10, after the merge of `cloud/catalog-integrity`.

17. **Fixed 2026-10-11** (branch `cloud/quality-1`): refused, per the vendored specs
    ("the actual type of the target instance"): a `@referredType` other than the type the
    response states is a 422 and nothing is stored; omitting it is still fine
    ([decisions/tmf-api.md](../decisions/tmf-api.md), "TMF API conventions";
    `tests/test_api_referred_type.py`). Original finding:
    **`@referredType` in a request is rewritten silently.** Every incoming reference is
    `app/api/common.py` `Ref`, which accepts any `@referredType` (or none) and never
    checks it; only `id` is used. The response then states the real type (`ref()`,
    `common.py`: `@referredType` = the resource type). So a POST naming a customer's
    `engagedParty` with `@referredType: "Organization"` for an Individual, or an order's
    `billingAccount` with any `@referredType`, is accepted and answered with a different
    value than was sent - the same pattern as #3 (role), which was fixed by refusing the
    mismatch (422). Not decided: refuse a wrong `@referredType`, or keep accepting it as
    TMF's informational field. No test sends a wrong one.
18. **A flaky test in `frontend/src/app/App.test.tsx`** was seen failing intermittently
    (reported by the human, 2026-10-10). Which test, the error and the run (cloud or
    workstation, UTC or the `TZ=America/New_York` pass) are not recorded here yet - add
    them when it fails next. Not seen in the `make check` run before the merge of
    `cloud/catalog-integrity` (2026-10-10: 184 passed in both time zones).
19. **Fixed 2026-10-11** (branch `cloud/quality-1`): the cause was the guess below -
    Testing Library's 1 s `waitFor` timeout against a cold first render on a loaded
    machine. Reproduced exactly by delaying the catalog's price load 1.2 s; the async
    utilities now wait up to 5 s ([decisions/frontend.md](../decisions/frontend.md),
    "Frontend conventions"). Original finding:
    **A flaky test in `frontend/src/screens/Catalog.test.tsx`**: `catalog > lists
    offerings with code, specification, price, period, status and validity` failed once
    with `Error: no row Mobile L` (thrown by `row()` inside `await waitFor(() =>
    row('Mobile L'))`; the test took 3.5 s, so the `waitFor` gave up before the rows
    rendered). Seen 2026-10-10 on the workstation, in the single vitest pass that `make
    progress` runs (`npx vitest run`, no `TZ`), during the merge of `cloud/subscriber-edit`;
    1 failed, 225 passed. Neither merged branch touched the catalog screen or its test.
    It passed on the immediate rerun and in every run after (the pre-commit hook and a
    full `make check`, both time zones each). Not reproduced or fixed; a likely cause is
    `waitFor`'s default 1 s timeout under load (15 jsdom environments, ~57 s of
    environment setup per run).
