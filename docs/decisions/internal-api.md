# Internal API (`/internal/v1`)

Refund/bill-delivery milestone: the transaction ledger also includes `payout`
(negative cash refund), while `refund` remains a positive ABR credit. Balance and
unallocated credit subtract consumed payout sources. See
[billing-completion.md](billing-completion.md) for the extended financial rules.

Decision records moved from CLAUDE.md (as of `910ec0a`), unchanged in meaning. The rules
needed every session stay in [CLAUDE.md](../../CLAUDE.md); `scripts/check-decisions.py`
proves nothing was lost in the move. Index: [README.md](README.md).

## Internal API (stage 5)

`app/internal/`, mounted at `/internal/v1`, serves what the admin UI cannot get from the
TMF APIs without re-implementing billing rules. Ours, not TMF; same conventions as
`app/api` (TMF `Error` bodies, Decimal-exact Money `{unit, value}`, one transaction per
request, the injected clock, offset/limit paging with `X-Total-Count`/`X-Result-Count`,
unknown query parameters are a 400), except that **dates are plain business dates**
(`YYYY-MM-DD` in BILLING_TZ). The contract is `spec/internal/bss-internal.oas.yaml`:
strict schemas (`additionalProperties: false`), validated for every response in the
suite by `tests/apiclient.py`, and the source of the UI's TypeScript types (never
FastAPI's `/openapi.json`). OAS 3.0 cannot make a `$ref` nullable (`nullable` next to
`allOf` does not admit null), so nullable variants are spelled out (`NullableMoney`, ...).

| Endpoint | What |
|---|---|
| `GET /context` | businessDate (injected clock), now, timeZone, utcOffset (at now), environment (`APP_ENV`), operator, currency (`UI_CURRENCY`, default RUB), billingRunAt (`BILLING_RUN_AT`, default 00:30), paymentTermDays (`BILL_PAYMENT_TERM_DAYS`) |
| `GET /billingAccount/{id}/settlement` | outstanding (remaining of every bill, on hold included), unallocatedCredit (payments and issued credit notes not applied yet), overdue {count, amount} (due before the business date, something remaining - display only), onHold {count, amount}, paymentTermDays. Invariant (tested through the API): outstanding - unallocatedCredit = -(balance + unbilled ABRs) |
| `GET /customerBill/{id}` | the bill dialog: billNo, dates, amountDue, remainingAmount, state, category, overdue, allowedStates (what PATCH accepts now, from `settlement.operator_states`), charges, sources (payments / credit notes, automatic / explicit, in order), history (from, to, changedAt, cause, operator) |
| `GET /billingAccount/{id}/firstCharge?productOffering.id=&startDate=` | dry run of an add order: the stage 3 core's `compute()` on the hypothetical product. period, cyclePeriod, billableDays of periodDays, price, amount, chargeRunDate = max(startDate, businessDate + 1), billDate = first cycle day >= chargeRunDate, next full period, balance, balanceAfter (= balance - this charge only), creditLimit, exceedsCreditLimit. startDate in the past or no price in the account currency -> 422 |
| `GET /billingAccount/{id}/products` | not-ended products (created, pendingActive, active, pendingTerminate, suspended) with offering name/code, specification name, price valid today (or on the start date), nextChargeDate (start date if not started, else the next cycle day after today; null if the product ends by then) |
| `GET /billingAccount/{id}/charges` | ABRs newest run first, with runDate, billableDays of periodDays (the proration), offering, bill |
| `POST /productOffering/{id}/priceChange` | the atomic price change of a live offering (see the TMF620 deviation above): ends the open price at midnight of `validFrom`, creates and attaches the successor; 201 `{productOffering, endedPrice {endsOn}, newPrice {price, validFrom}}` |
| `GET /billingAccount/{id}/transactions` | charges, refunds and payments as one ledger with balanceAfter. Positive = credit (as accountBalance); ordered by day (ABR: run date; payment: its date in BILLING_TZ), ABRs before payments on the same day (the run of D happens at its start), then timestamp, id. The newest balanceAfter equals the account balance (tested) |
| `GET /billingRun` | stage 4: runs newest first - status, failureKind, trigger, attempt, times, accounts {total, ok, failed, blocked} (derived: members = accounts opened before 00:00 of the date), charges {count, amounts per currency}, error, isLatest |
| `GET /billingRun/{id}` / `.../accounts?status=` | one run; its per-account outcomes (account, status, error, waitsFor run, attempts) |
| `POST /billingRun/catchUp {upTo}` | queues a catch-up; **the API never bills**. upTo after the business date, or today before `BILLING_RUN_AT` (Q5) -> 422. 202 `{request, dates}` (a run that failed as a whole first, then every date after the latest run); 200 `{dates: []}` and nothing queued when nothing is due |
| `POST /billingRun/{id}/retry` | queues a retry of any failed run (Q3); 409 if not failed or a retry is already queued; 202 `{request}` |

"The TMF620 deviation above" is D-W21 in [catalog.md](catalog.md#tmf620-mapping).

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
