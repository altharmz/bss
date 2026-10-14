## Behaviour and deviations

- **Creation is run-only.** Bills and ABRs are created only by the billing run
  (`app/billing/adapter.py`, called by the worker). Bills support state-only PATCH
  to hold/release; ABRs remain read-only. POST/DELETE are 405.
  Both lists are empty until a run has billed something
  (`::test_bill_and_rate_lists_are_empty_before_any_billing_run`).
- `appliedBillingRateType`: `recurring` -> `appliedBillingCharge`, `refund` ->
  `appliedBillingCredit`. `taxExcludedAmount` is negative for refunds. `periodCoverage`
  and `billingPeriod` are half-open date ranges sent as BILLING_TZ midnights.
  `isBilled` / `bill` say whether a bill collected it (`::test_applied_billing_rates`,
  `::test_customer_bill_and_is_billed`).
- `date` of an ABR is its `applied_at` (the run attempt's injected clock); billing uses the
  **run date** of the run that wrote it (see [domain.md](../domain.md#bills)).
- The run issues bills (new -> validated -> sent); payments and unused credit settle
  them. PATCH holds/releases a bill; remaining amounts and allocations are returned
  (`tests/test_api_bill_settlement.py`).
- `billCycle` links to the issuing daily run, exposed by read-only `/billCycle` and
  `/billCycle/{cycle_id}` with standard fields/paging and a `billingDate` filter.
  A cycle's `billingPeriod` is an ISO date string; the account's monthly date range
  stays on the bill. `nextBillDate` is the next account cycle day after this bill's
  own date, unaffected by today's clock. Tax-included/excluded amounts equal the
  issued `amountDue` (no taxes), including negative credit notes.
  `tests/test_api_read_completeness.py` checks links, dates, fields and MCP tools.
- `/customerBillOnDemand` is not implemented.
