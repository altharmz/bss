## Behaviour and deviations

- **Read-only.** Bills and ABRs are written only by the billing run
  (`app/billing/adapter.py`, called by the worker); POST/PATCH/DELETE are 405
  (`app/api/bill.py`; `tests/test_api_inventory_bill.py::test_bills_and_rates_are_read_only`).
  Both lists are empty until a run has billed something
  (`::test_bill_and_rate_lists_are_empty_before_any_billing_run`).
- `appliedBillingRateType`: `recurring` -> `appliedBillingCharge`, `refund` ->
  `appliedBillingCredit`. `taxExcludedAmount` is negative for refunds. `periodCoverage`
  and `billingPeriod` are half-open date ranges sent as BILLING_TZ midnights.
  `isBilled` / `bill` say whether a bill collected it (`::test_applied_billing_rates`,
  `::test_customer_bill_and_is_billed`).
- `date` of an ABR is its `created_at` (the database's clock); billing itself uses the
  **run date** of the run that wrote it (see [domain.md](../domain.md#bills)).
- Bills are written with `state: new` and never change state here; there is no
  settlement of bills by payments (the balance is account-level).
- `/billCycle` and `/customerBillOnDemand` are not implemented.
