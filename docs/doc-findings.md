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
   with `"status": "ok"`. CLAUDE.md ("Deployment (slice f)") states the same "200 must say
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
3. **`billingAccount.relatedParty[].role` is not checked.** CLAUDE.md's TMF666 mapping
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
5. **CLAUDE.md calls the frontend planned.** "Domain model & decisions" says "`frontend/`
   is planned (stage 5)"; it is built and deployed (`frontend/`, 10 of 13 UI items in
   PROGRESS.md; first web deploy `9424b9b` per CLAUDE.md itself).
6. **No `/backup-status`.** The task brief for these docs mentions "backups and
   /backup-status"; no such endpoint, script or check exists in this repository. Backups
   are vps-infra's (CLAUDE.md, "Shared infrastructure"); operations.md says so.

## Behaviour that is not tested (or not guarded)

7. **Lifecycle of specifications, prices and customers is unguarded.** Only the
   offering's `lifecycleStatus` is restricted to forward steps; PATCH may set a
   specification's or a price's `lifecycleStatus`, and a customer's `status`, to any value
   (`app/api/catalog.py`, `app/api/customer.py`). Whether that is intended is not written
   down; no test covers it.
8. **An offering's `validFor` can be changed freely by PATCH** (`app/api/catalog.py`,
   `patch_offering`): moved into the past, or ended before live products' dates. It only
   affects new orders (`offeringNotValid`), not billing, but no rule or test covers it.
9. **`creationDate` (TMF637 product) and `date` (TMF678 ABR) come from the database's
   `now()`**, not the injected clock - the mapping in CLAUDE.md says so, but it means
   seeded demo data shows the seed's run time there, not the FixedClock dates the seed
   simulates. Every other business instant is from the clock.
10. **Bills never leave `state: new`** and nothing settles a bill against payments; the
    balance is account-level only. TMF678's other states are in the enum but unused.
11. **Product statuses `pendingActive`, `suspended` and `aborted` are never set** by any
    code path (the enum is TMF's); the worker's flip handles `pendingActive` anyway.

## Unclear areas and environment notes

- **`billing_run.started_at` on a takeover** is kept (`app/repository/billing.py`,
  `take_over_run`), while a reopen after a failure resets it. So `/billing-status`
  measures a taken-over run's 2 h grace from the original start. Not stated in CLAUDE.md;
  the column comment of migration 0005 says it.

12. **The spec files' server URLs differ from our base paths** (TMF629's spec says
    `/tmf-api/customer/v5/`, TMF676's `basePath` is `/payment/v4/`, others have no
    `/tmf-api` prefix). The paths here are a project decision (CLAUDE.md, "TMF API
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
