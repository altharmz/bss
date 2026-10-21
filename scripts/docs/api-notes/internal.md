## Behaviour

Ours, not TMF: what the admin UI cannot get from the TMF APIs without re-implementing
billing rules (`app/internal/__init__.py`). Same conventions as the TMF APIs (TMF `Error`
bodies, Decimal-exact Money, one business transaction per request, the injected clock,
offset/limit paging with `X-Total-Count`/`X-Result-Count`, unknown query parameters are a
400), except that **dates are plain business dates** (`YYYY-MM-DD` in BILLING_TZ). The
contract is [`spec/internal/bss-internal.oas.yaml`](../../spec/internal/bss-internal.oas.yaml):
strict schemas (`additionalProperties: false`), validated for every response in the suite.

Authentication is enforced by the app: a valid session or service credential is required.
Session validation uses a separate short transaction. Session writes require
`X-Bss-Request: 1`, including sign-in. User management requires an admin session;
service credentials cannot access user/session endpoints. Password changes revoke all
sessions, including the current one. See [users and sessions](../decisions/users.md).

| Endpoint | What | Proven by |
|---|---|---|
| `GET /context` | businessDate (injected clock), now, timeZone, utcOffset (at now), environment (`APP_ENV`), operator (authenticated session username, or a verified MCP/service principal; an arbitrary `X-Remote-User` is ignored), currency (`UI_CURRENCY`), billingRunAt | `tests/test_internal_api.py::test_context`, `::test_context_business_date_is_the_date_in_billing_tz`, `::test_context_operator_ignores_the_proxy_header` |
| `GET /billingAccount/{id}/firstCharge` | dry run of an add order: the billing core's `compute()` on the hypothetical product - period, billable days of period days, price, amount, `chargeRunDate` = max(startDate, businessDate + 1), `billDate` = first cycle day >= chargeRunDate, the next full period, balance, `balanceAfter` (balance minus this charge only), `exceedsCreditLimit`. startDate in the past or no price in the account currency: 422 | `::test_first_charge_mid_period_today`, `::test_first_charge_rounds_half_even`, `::test_the_preview_is_what_the_billing_run_writes`, `::test_first_charge_errors` |
| `GET /billingAccount/{id}/products` | not-ended products (created, pendingActive, active, pendingTerminate, suspended), oldest start first, with offering name/code, specification, the price valid today (or on the start date), `nextChargeDate` | `::test_open_products_with_next_charge_and_price` |
| `GET /billingAccount/{id}/charges` | ABRs newest run first, with run date, billable days of period days, offering, bill | `::test_charges` |
| `GET /billingAccount/{id}/transactions` | charges, refunds and payments as one ledger with `balanceAfter` (positive = credit); ordered by day (ABR: run date; payment: its date in BILLING_TZ), ABRs before payments on the same day | `::test_transactions_carry_the_running_balance` |
| `POST /productOffering/{id}/priceChange` | **deviation from TMF620 (D-W21)**: in one transaction, ends the open-ended recurring price in `currency` at midnight of `validFrom` and creates the successor, attached, from that moment. `validFrom` >= tomorrow (422); no open-ended price in that currency, or a price already starting on or after `validFrom`: 409. 201 `{productOffering, endedPrice {endsOn}, newPrice {price, validFrom}}` | `tests/test_internal_price_change.py::test_a_live_price_changes_in_one_step`, `::test_refusals`, `::test_a_failure_half_way_changes_nothing` |
| `GET /billingRun`, `GET /billingRun/{id}`, `GET /billingRun/{id}/accounts?status=` | runs newest first with status, failureKind, trigger, attempt, times, account counts (derived from the run's members), charges per currency, error, isLatest; one run's per-account outcomes | `tests/test_worker_queue.py::test_runs_are_listed_newest_first_with_derived_counts`, `::test_a_runs_accounts_with_their_outcomes` |
| `POST /billingRun/catchUp {upTo}` | **queues** a catch-up - the API never bills. `upTo` after the business date, or today before `BILLING_RUN_AT`: 422. 202 `{request, dates}`; 200 `{dates: []}` and nothing queued when nothing is due | `::test_catch_up_is_refused_before_the_run_is_due`, `::test_catch_up_queues_a_request_and_says_which_dates`, `::test_catch_up_when_nothing_is_due_queues_nothing` |
| `POST /billingRun/{id}/retry` | queues a retry of a failed run; 409 if the run is not failed or a retry is already queued; 202 `{request}` | `::test_retry_queues_a_request_for_a_failed_run_once`, `::test_only_a_failed_run_can_be_retried` |

Prepaid products additionally expose `authorizedAt`, exclusive `paidThrough`,
`suspensionCause` (manual/account/funding) and retained `needs_topup` amounts and
observation instant. Their effective status follows the injected business date;
reads never write or emit expiry events. The worker persists observed transitions.
`nextChargeDate` is their paid-through boundary. See `tests/test_prepaid_entitlement.py`.

The admin UI's Billing runs screen uses all five billing-run endpoints (see
[ui.md](../ui.md#billing-runs-uibillingrunslist-uibillingrunscatchup-uibillingrunsretry---screensbillingrunstsx));
`tests/test_ui_requests.py` replays its catch-up and retry requests against the real
API and worker.
