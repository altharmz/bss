# Internal API (`/internal/v1`)

Refund/bill-delivery milestone: the transaction ledger also includes `payout`
(negative cash refund), while `refund` remains a positive ABR credit. Balance and
unallocated credit subtract consumed payout sources. See
[billing-completion.md](billing-completion.md) for the extended financial rules.

Index: [README.md](README.md).

## Internal API (stage 5)

`app/internal/`, mounted at `/internal/v1`, serves what the admin UI cannot get from the
TMF APIs without re-implementing billing rules. Ours, not TMF; same conventions as
`app/api` (TMF `Error` bodies, Decimal-exact Money `{unit, value}`, one transaction per
request, the injected clock, offset/limit paging with `X-Result-Count` and `Link: rel="next"` (no `X-Total-Count` since #312),
unknown query parameters are a 400), except that **dates are plain business dates**
(`YYYY-MM-DD` in BILLING_TZ). The contract is `spec/internal/bss-internal.oas.yaml`:
strict schemas (`additionalProperties: false`), validated for every response in the
suite by `tests/apiclient.py`, and the source of the UI's TypeScript types (never
FastAPI's `/openapi.json`). OAS 3.0 cannot make a `$ref` nullable (`nullable` next to
`allOf` does not admit null), so nullable variants are spelled out (`NullableMoney`, ...).

| Endpoint | What |
|---|---|
| `GET /context` | businessDate (injected clock), now, timeZone, utcOffset (at now), environment (`APP_ENV`), operator, currency (`UI_CURRENCY`, default USD), billingRunAt (`BILLING_RUN_AT`, default 00:30), paymentTermDays (`BILL_PAYMENT_TERM_DAYS`) |
| `GET /billingAccount/{id}/settlement` | outstanding (remaining of every bill, on hold included), unallocatedCredit (payments and issued credit notes not applied yet), overdue {count, amount} (due before the business date, something remaining - display only), onHold {count, amount}, paymentTermDays. Invariant (tested through the API): outstanding - unallocatedCredit = -(balance + unbilled ABRs) |
| `GET /customerBill/{id}` | the bill dialog: billNo, dates, amountDue, remainingAmount, state, category, overdue, allowedStates (what PATCH accepts now, from `settlement.operator_states`), charges, sources (payments / credit notes, automatic / explicit, in order), history (from, to, changedAt, cause, operator) |
| `GET /billingAccount/{id}/firstCharge?productOffering.id=&startDate=` | dry run of an add order: the stage 3 core's `compute()` on the hypothetical product. period, cyclePeriod, billableDays of periodDays, price, amount, chargeRunDate = max(startDate, businessDate + 1), billDate = first cycle day >= chargeRunDate, next full period, balance, balanceAfter (= balance - this charge only), creditLimit, exceedsCreditLimit. startDate in the past or no price in the account currency -> 422. A prepaid account (P5a) pays the first interval in full from startDate, tax included, and adds `required`, `available`, `shortfall` ([prepaid.md](prepaid.md#p5a-enable-prepaid-accounts-2026-10-23)) |
| `GET /billingAccount/{id}/products` | not-ended products (created, pendingActive, active, pendingTerminate, suspended) with offering name/code, specification name, price valid today (or on the start date), nextChargeDate (the first future run that will write a recurring charge, using the billing core and stored ABRs; prepaid keeps its paid-through renewal date), msisdn, imsi and iccid live today (null without one, #456) |
| `GET /billingAccount/{id}/charges` | ABRs newest run first, with runDate, billableDays of periodDays (the proration), offering, bill |
| `POST /productOffering/{id}/priceChange` | the atomic price change of a live offering (see the TMF620 deviation above): ends the open price at midnight of `validFrom`, creates and attaches the successor; 201 `{productOffering, endedPrice {endsOn}, newPrice {price, validFrom}}` |
| `GET /billingAccount/{id}/transactions` | charges, refunds and payments as one ledger with balanceAfter. Positive = credit (as accountBalance); ordered by day (ABR: run date; payment: its date in BILLING_TZ), ABRs before payments on the same day (the run of D happens at its start), then timestamp, id. The newest balanceAfter equals the account balance (tested) |
| `GET /billingRun` | stage 4: runs newest first - status, failureKind, trigger, attempt, times, accounts {total, ok, failed, blocked} (derived: members = accounts opened before 00:00 of the date), charges {count, amounts per currency}, error, isLatest |
| `GET /billingRun/{id}` / `.../accounts?status=` | one run; its per-account outcomes (account, status, error, waitsFor run, attempts) |
| `POST /billingRun/catchUp {upTo}` | queues a catch-up; **the API never bills**. upTo after the business date, or today before `BILLING_RUN_AT` (Q5) -> 422. 202 `{request, dates}` (a run that failed as a whole first, then every date after the latest run); 200 `{dates: []}` and nothing queued when nothing is due |
| `GET`/`PUT /logging` | issue #274, administrators only: the runtime log level `{baseLevel, envLevel, effectiveLevel, debugUntil, updatedAt, updatedBy}`. PUT `{baseLevel?: WARNING\|INFO\|null, debugMinutes?: 0..120}` (strict ints, at least one field, a bad value or unknown attribute is a 400; `debugMinutes: 0` ends debug now); writes one `log_setting_change` row and one WARNING log line in the transaction. Not a model tool |
| `GET /service/log-level` | the MCP connector's poll (it has no database): `{baseLevel, effectiveLevel, debugUntil}`. The service credential alone, no principal; a session, the smoke credential or a principal get 403. In `AUTH_OPERATIONS`, never a tool. See [infra.md](infra.md#conventions), "Logging" |
| `POST /billingRun/{id}/retry` | queues a retry of any failed run (Q3); 409 if not failed or a retry is already queued; 202 `{request}` |

"The TMF620 deviation above" is D-W21 in [catalog.md](catalog.md#tmf620-mapping).

- **Superseded by application authentication (2026-10-19):** operator now comes from
  the authenticated session, or from a verified service credential plus a validated
  MCP principal. An arbitrary `X-Remote-User` has no effect. See [users.md](users.md).
  The following describes the former boundary, retained as historical context.
- **operator** comes from the `X-Remote-User` header, which the shared Caddy must *set*
  (overwrite) from the basic-auth user, so a client-sent header never passes through.
  Anything not matching `^[A-Za-z0-9._@:-]{1,64}$` is reported as null. The MCP connector
  sets it to `mcp:<user or token name>` ([mcp.md](../mcp.md)); `:` was added for it
  (2026-10-11). A basic-auth user cannot contain `:` (it is the user/password separator),
  so an operator with `:` always comes from MCP.
- **Trust in `X-Remote-User`.** The app believes the header only because, from outside the
  VPS, it is reachable solely through Caddy. It is not proof of identity: any other
  container on the `shared` network can reach `bss:8080` directly and forge it, and so can
  anyone with a shell or an SSH tunnel on the VPS (the app is also published on the host's
  `127.0.0.1:18081`, which `make tunnel` uses). Acceptable for this demo because the value
  is display-only - nothing is authorised by it. Anything that needs a real operator
  identity must not rely on this header.
- Every rule above was broken once to see a test fail (extra attribute vs the strict
  contract, charge run date without the end-of-day rule, ledger sign, creditLimit
  currency, duplicate code).
- The billing run endpoints (stage 4 slice h; table above) follow the shape agreed in
  stage 5 checkpoint 1, with retry of **any failed run** (decided in stage 4, superseding
  "latest run only"; the worker still keeps each account's dates in order: retrying D+1
  retries only the accounts that failed in D+1, accounts blocked on D stay blocked until
  D is fixed). `tests/apiclient.py` maps `/billingRun/<uuid>` to `/billingRun/{id}`
  unless the path is a declared literal (`/billingRun/catchUp`).

- `GET /internal/v1/eventSubscription` (slice 23): an operator view of the live hubs, like the prepaid
  commands; not a TMF extension. Entry: [frontend.md](frontend.md#slice-23-event-subscriptions-hubs-and-system-panel-2026-10-26).

- GUI review G2b (B1/I5): open products expose nullable `undoableStatusChange`
  `{date, status, productOrder: {id}}` for an applied status change today. Eligibility
  checks the completed order's items with the cancellation rules, including termination
  and number constraints; yesterday's and cancelled changes are absent. The read makes
  no mutation. Same-day suspension/resumption replay tests pin cancellation and restored
  status; future suspension retains its existing pending Undo. No migration or TMF change.

G5 / B8: Products started today first become visible to tomorrow’s run. Uncharged earlier periods also point to tomorrow; already charged periods point to the next due period. The projection calls the billing core with stored ABRs at the candidate run dates, including offering boundaries, without writing anything. A product ending before renewal can still have an uncharged first period. Prepaid continues to expose paidThrough as its next renewal.

- Registry lists and searches (#314, #330): `GET /internal/v1/subscriber`, `/party` and
  `/partyCustomer` page `ORDER BY created_at, id` with OFFSET. Enum filters are compared
  as the enum (`state = %s::account_state`), never as `::text`: as text they lost their
  statistics and planned full scans. An unsearched page is cut from its own table and
  only its rows are joined (customer, party). A search (`q`) goes through
  `app/repository/subscribers.py` `matching`: one probe collects up to `MATCH_CAP`
  (1000) distinct matches with the filters; fewer are sorted and the page is read by id,
  more walk the `(created_at, id)` index testing the same conditions OR-ed. No
  `id IN (... UNION ...)`: the planner cannot know how many rows a prefix matches, and
  hashing every match cost 20-60 ms (narrow) to 1.6 s (one letter) at 100k accounts.
  Measurements: [loadtest-plan-sweep.md](../loadtest-plan-sweep.md#registry-lists-and-searches-330).
  A match can have a probe form and a page form (#365, 2026-11-21). The subscriber number
  probes from the number side (a lateral nested loop from each matching number to its
  account) and its page tests each account with a correlated EXISTS, never the set of
  every matching number. The probe is read through a server-side cursor and stops at
  MATCH_CAP + 1 distinct rows. A broad number prefix went from 1.3-1.9 s to ~75 ms:
  [loadtest-plan-sweep.md](../loadtest-plan-sweep.md#subscriber-number-prefixes-365).
  Each subscriber row also carries `msisdns` and `msisdnCount` (#456, 2026-11-24): up to 3
  numbers live today on the account's not-ended products, read by one windowed query for
  the page ([frontend.md](frontend.md#subscriber-numbers-in-the-lists-2026-11-24-issue-456)).

- `GET /internal/v1/report/kpi` (issue #198): prepaid KPIs by month (lines, net adds, churn,
  billed revenue by source, ARPU), read-only and computed on request. Entry:
  [reporting.md](reporting.md#prepaid-kpis-by-month-2026-11-15-issue-198).
