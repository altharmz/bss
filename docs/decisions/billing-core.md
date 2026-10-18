# Billing core (stage 3) and its open questions

Decision records moved from CLAUDE.md (as of `910ec0a`), unchanged in meaning. The rules
needed every session stay in [CLAUDE.md](../../CLAUDE.md); `scripts/check-decisions.py`
proves nothing was lost in the move. Index: [README.md](README.md).

## Billing (stage 3)

`app/billing/core.py` is pure: no database, no clock, no I/O. `compute(state, as_of, tz)`
takes one account's `BillingState` (frozen dataclasses from `model.py`) and returns the
ABRs and bills to write. `app/billing/adapter.py` loads the state through the repository
(`app/repository/billing.py` maps rows <-> dataclasses; the core never sees a dict) and
writes the result: `bill_account(conn, account_id, run)`, one transaction per account
(a savepoint inside the caller's). The stage 4 worker calls it once per account per run.

**Periods.** Calendar-aligned to `bill_cycle_day` D: `[D of month M, D of month M+1)`,
28-31 days. The cycle day is assumed fixed (see [Open questions](#open-questions)).

**When a period is charged: in advance, once its first billable day is <= as_of.** The
first billable day is `max(period start, start_date)`. Charging waits for the product's
start, not just for the period's: the price is looked up at that day's midnight, which
must be in the past when the charge is written (a future midnight's price can still
change). Consequence: **a product cancelled before its start is never charged**.

**Price.** The offering's monthly recurring price in the account's currency, valid at
midnight (BILLING_TZ) of the period's first billable day. A price change mid-period
applies from the next period. None found -> `MissingPrice` naming product, day and
currency (never skipped); more than one -> `InconsistentState`. Prices need no visibility
filter (unlike orders, below): a price change only takes effect at or after the moment it
was made (prices never start in the past and are never ended retroactively), and the core
only looks prices up at midnights <= as_of, so a replayed earlier date can never see a
later change.

**Expected-vs-actual diff, per product and period:**

- `expected = round_half_even(price x billable_days / period_days, 0.01)`, rounded once.
  `billable_days` counts `start_date <= d < termination_date` inside the period; zero for
  `cancelled`/`aborted`. Every other status is billed by dates alone (`created`,
  `suspended`, `pendingTerminate`, ...).
- Nothing stored -> a `recurring` charge for the billable range, even if it rounds to 0.00.
- A charge stored -> first re-check it still equals `round(price x its days /
  period_days)` (else `InconsistentState`), then `diff = expected - (charge + refunds)`:
  0 -> nothing; < 0 -> a refund of `diff` (negative) for the unbilled tail
  `[termination_date, charge end)`, or the whole charge when nothing is billable any more,
  `refunds_rate_id` -> the charge; > 0 -> `InconsistentState` (impossible by the rules).
- The refund is never priced on its own days: `charge + refund` must equal one correct
  rounding of the used days, or cent drift accumulates (`round(p x 29/30) - round(p x
  22/30)` != `round(p x 7/30)` for p = 0.02; locked by
  `test_refund_keeps_the_period_net_exact`).
- **One refund per charge** (`refunds_rate_id UNIQUE`, and refunds may not overlap).
  Enough because termination dates are immutable: a product is deleted once (a second
  delete is rejected `productAlreadyEnded`), so the expected net of a period drops at most
  once. A second reduction would raise `InconsistentState`.
- If the termination is already known when the period is charged, there is one reduced
  charge and no refund. Daily runs and one catch-up run can therefore store different rows
  (charge + refund vs one reduced charge) with the same net per period.

**Bills.** A bill on cycle day D covers `[D, next D)` and collects every unbilled ABR of
the account whose **run date** (`billing_run.run_date` of the run that wrote it) is <= D.
A run issues the bills for every cycle day after the account's last bill up to as_of;
one that would collect nothing is skipped (a 0.00 bill of 0.00 charges is still issued).
`amount_due` is the sum and may be negative. ABRs written in the run of D join the bill
of D; a product starting mid-cycle, or a late ABR, goes onto the next bill. Run date, not
`created_at`: `created_at` is the database's `now()`, outside the injected clock, and a run
for D that crosses midnight or is retried the next day would date its ABRs D+1.

**What a run sees: order-driven facts completed before 00:00 of its run date** in
BILLING_TZ (end-of-day processing of D-1). The adapter filters products by the
`completion_date` of the order that created them, and terminations/cancellations by the
order that set them (an invisible termination is passed with the status the product had
before it). An order placed on D is first seen by the run of D+1, whatever time the run
of D executes. This makes a replayed date see exactly what the daily run saw.

**Concurrency and guards.** `bill_account` locks the billing account row (`FOR UPDATE`)
before reading: a concurrent second call waits, then sees the first one's ABRs and writes
nothing (without the lock it collides on `abr_no_overlap`; proven with two connections
in `tests/test_billing_db.py`). It only writes into a run that is `running` (re-read
`FOR SHARE`). Any `BillingError` rolls the whole account back.

**Tests.** `tests/test_billing_core.py`: the hand-checked examples (half-even 1.62 vs
1.63, 300 x 17/31, 27/28 vs 27/29 days, refund -116.13, ...). `tests/test_billing_properties.py`
(hypothesis, pinned exactly): idempotence, net per period = one rounding, lifetime without
drift, refund shape, no overlap, precision/currency, bills collect every ABR exactly once,
replay identical, daily vs one catch-up run equal in non-zero net per period. The oracle
counts billable days one by one, independently of `app/billing`. `tests/test_billing_db.py`:
the same through the adapter and the real schema, plus the TMF678 view, visibility on
the day boundary and replay across an outage. Every rule above was broken once to see a
test fail (half-up rounding, refund priced separately, overlapping charges, visibility `<=`,
termination always visible, no account lock, no running-run guard).

**Stage 4 must:**

- run the date D only at or after 00:00 of D (BILLING_TZ). A run for D started earlier
  would miss orders completed between its start and midnight, which a replay would see;
- after an outage, replay every missed date in order, each with its own `billing_run`, so
  run dates and bills match daily runs. A single multi-month `as_of` call is for tests
  only (it puts every catch-up ABR on the bill after as_of);
- call `bill_account` per account and record a failing account without stopping the run.

## Taxes (2026-10-18)

Slice 7 of ["Following TMF"](tmf-api.md#following-tmf-2026-10-17); retires D4 (taxes out
of scope). The specs define the shape (TMF620 `TaxItem` on a price, TMF678
`AppliedBillingTaxRate` on an ABR and `TaxItem` on a bill, TMF676 `taxAmount`, TMF666
`TaxExemptionCertificate`), not the rates or the arithmetic; the three open choices were
left to "what TMF recommends" by the human. Migration `0025`. Five lines removed from
`tests/tmf-gaps.txt` (161 -> 156 gaps): ABR `appliedTax` and `taxIncludedAmount`, bill
`taxItem`, payment and refund `taxAmount`.

- **The rates come from the price.** A price's `tax[]` is what billing applies: no tax
  engine, no jurisdiction table (neither is in the vendored specs). `price` stays the
  amount before tax. Each item needs a `taxCategory` (once per price) and a `taxRate`
  (`tax_problems`, 422 with the item's path, inline prices and imports included) - our
  requirement, because billing cannot apply or group a tax without them. A `taxAmount`,
  if sent, must be the tax of a full month (`price x rate / 100`, in the price's
  currency); a new `price` must come with matching amounts. The items stay the stored
  document of slice 6 (`product_offering_price.details.tax`), read by the adapter.
- **`taxRate` is a percent, 0..100** (20 = 20%). The specs disagree: TMF620's examples
  (all nine) and TMF637's say 20 and 15, TMF666's exemption text speaks of "0% ... 5% if
  the actual rate was 10%", TMF678's examples say 0.2 and 0.25 in seven places and 20 in
  one. Percent is the majority and the convention where rates are entered. A client
  sending a fraction (0.2) gets 0.2%; nothing can tell the two apart.
- **A tax is part of the charge** ([catalog.md](catalog.md#catalog-attributes-2026-10-18)):
  it changes on PATCH only while the price has priced nothing (409 "end its validFor and
  attach a new price" otherwise). So the core's check that a stored charge matches its
  price covers its tax lines too, and a rate change is a new price from a future instant,
  like any price change.
- **Arithmetic.** Per ABR and tax item: the prorated net x rate / 100, rounded once,
  half-even, to cents (`core.tax_of`) - the rate of the price valid at the charge's first
  billable day. A refund's line is `tax_of(expected net, the charge's rate)` minus the
  charge's line: the same expected-vs-actual diff as the net, so cents never drift and
  nothing that changed after the charge (an exemption, a rate) reaches it. A stored
  charge's lines must be the price's items in order, each at the price's rate or 0, each
  `tax_of` the charge's amount; a refund's must be the diff - else `InconsistentState`.
- **Exemptions are applied** (TMF666: certificates "that can be applied when
  calculating charges levied to the account", "per tax type"). A billing account's
  `taxExemption[].taxDefinition[].taxType` equal to an item's `taxCategory` makes that
  line rate 0 / amount 0 - still listed, as the spec's "0% would be a full exemption" -
  when the certificate's and the definition's `validFor` (absent: unbounded) both cover
  midnight of the charge's first billable day. `TaxDefinition` has no rate attribute,
  so a partial exemption cannot be expressed and is not implemented; a certificate
  without a typed definition exempts nothing. A party's or customer's
  `taxExemptionCertificate` stays a record (only the account's are "levied to the
  account").
- **What carries tax.** ABR: `appliedTax[]` (`taxCategory`, `taxRate`, `taxAmount`,
  `@type: AppliedBillingTaxRate`), `taxExcludedAmount` (the stored `amount`),
  `taxIncludedAmount`. Stored as `applied_tax` jsonb + `tax_amount` (their sum). Bill:
  `amountDue` = `taxIncludedAmount` = the sum of its ABRs with tax (as written by the
  run or the on-demand bill), `taxExcludedAmount` = their nets, `taxItem[]` = their lines
  summed per category and rate, not rounded again (`billing.bill_taxes`; no new bill
  column). The bill document (detailed/summary JSON) gains `taxExcludedAmount`,
  `taxItem`, and per charge `taxIncludedAmount`.
- **Debt is tax-included everywhere**: the account balance, the issue check (bill = its
  ABRs), settlement, the ledger, a run's totals and the internal views (`Charge.amount`,
  the first-charge preview's `amount`/`balanceAfter`; the preview applies the account's
  exemptions). Existing ABRs have no lines (`[]`, 0), so nothing already stored changes
  its value.
- **Price views (TMF622/637).** `Price.dutyFreeAmount` is the monthly price,
  `taxIncludedAmount` adds each item's `tax_of` a full month, `taxRate` is the sum of the
  rates. They show the catalog's taxes, not the account's exemptions (the ABRs show what
  was applied). An order total over items with different rates has no `taxRate`.
- **TMF676.** `amount` is net, `taxAmount` the tax, `totalAmount` both: either part gives
  the other, both must add up, the same currency, `0 <= taxAmount <= totalAmount` (422
  otherwise); none sent = no tax. Stored as `tax_amount` next to `amount` (the total,
  which is what settles bills, as before). A `paymentItem` allocates `amount + taxAmount`
  (`totalAmount`, if sent, must equal it); an item in a response still answers the
  allocation as `amount` and `totalAmount` (allocations have no tax split). Refunds the
  same; a refund is immutable, so `tax_amount` is in its INSERT.
- **Migration `0025`** refuses to run if a stored price tax would fail the rules above or
  an offering already charged has a taxed price (its charges would not match): both can
  exist only where `0024` ran, which is not deployed. Rolling back needs `alembic
  downgrade 0024` by hand (drops the tax columns; ABR tax lines are lost).
- Not in this slice (no TMF behaviour behind it, or another slice): the UI and the seed
  (the UI shows `amountDue`, now with tax), TMF622 `TaxDefinition` on order items.

Tests: `tests/test_api_taxes.py` (ABR and bill lines, settlement and balance, two items
per price, refund, exemptions and their validity, price views, the price rules, payment
items and refunds), the tax cases in `tests/test_billing_core.py`,
`test_amount_and_tax_amount_split_the_total` and the refused splits in
`tests/test_api_payment.py`, `test_0025_taxes`. Negative proofs: exemptions ignored,
a refund taking back the whole tax, bills due without their taxes, a tax change not
counted as a charge change, and a payment item allocating its `amount` alone each failed
them (the last one only after the item test was made to exceed the bill).

## Open questions

- **Changing `billCycleDay`.** PATCH is a 409 today. Not settled in stage 3: a change has
  to decide how the current period is treated (close it early, stretch it, or only apply
  from the next period). The billing core assumes a fixed cycle day; with a changed one,
  stored ABRs no longer sit inside one cycle period and `compute` raises
  `InconsistentState`.
- **Suspension semantics.** Billing is date-driven: `Suspended`/`Closed` billing accounts
  and `suspended` products are billed like any other. Whether a suspension should stop or
  reduce charges (and how a resume re-enters billing) is open; ordering already refuses
  new orders on a non-Active account.

The resolved items of CLAUDE.md's old "Open questions" moved too: stale billing runs and
`APP_ENV` to [worker.md](worker.md#resolved-questions), the two template ports to
[infra.md](infra.md#to-port-to-vps-lab-template).
