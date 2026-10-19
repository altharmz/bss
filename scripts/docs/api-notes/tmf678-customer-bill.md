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
  own date, unaffected by today's clock. `tests/test_api_read_completeness.py` checks
  links, dates, fields and MCP tools.
- Taxes: `taxIncludedAmount` = `amountDue`; `taxExcludedAmount` is the charges before
  tax; `taxItem[]` sums the charges' tax lines per category and rate. An ABR carries
  `appliedTax[]` and `taxIncludedAmount` (`tests/test_api_taxes.py`,
  [taxes](../decisions/billing-core.md#taxes-2026-10-18)).
- /customerBillOnDemand exposes list/retrieve/create of durable requests. The worker
  waits for a successful account run that sees orders completed on the request day,
  then issues one offCycle bill from posted unbilled ABRs, or rejects if none remain.
  Request state and bill commit together; replay is harmless. Monthly anchors stay
  unchanged. Failed/blocked account runs keep requests pending; validation errors
  terminate the request with rollback of its financial writes.
- Newly issued bills carry billDocument: a base64 JSON attachment with exact totals,
  optional charge detail, and snapshotted presentation preferences. Configuration
  and settlement changes do not rewrite it. Legacy documents are not fabricated.
  See [billing completion](../decisions/billing-completion.md). It carries `id` (the
  bill's), `size` (bytes) and `description`; being embedded, it has no `href`, `url` or
  `validFor` (accepted deviations in `tests/tmf-gaps.txt`).
- An ABR's `name` is its offering's, `description` what it bills (`<offering>, <first
  day> - <last day>`; a refund `refund of ...`), `characteristic` empty. Every
  `billingAccount` reference states `ratingType: postpaid`; `bill` references carry the
  bill number as `name` (`tests/test_api_last_gaps.py`).
- A bill names its account's customer (`relatedParty`, role `customer`), its account's
  `financialAccount` (if linked) and `defaultPaymentMethod` (as `paymentMethod`, if set).
- A bill cycle (a daily run) states `chargeDate` = `mailingDate` = `creditDate` = its
  date, `paymentDueDate` = the due date of the bills it issued on its date (else its date
  + `BILL_PAYMENT_TERM_DAYS`), and `BillCycleSpecification` = the monthly policy.
- /customerBillOnDemand stores and returns `relatedParty` as sent.
