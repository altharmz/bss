# Billing core (stage 3) and its open questions

The monthly-only charge policy below is extended by
[configurable charge periods](charge-periods.md) (2026-10-20). Monthly length-1 prices
keep the original calculation; new cadences use activation-date anniversaries.

Index: [README.md](README.md).

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

## Offering changes (2026-10-18)

Slice 8b of ["Following TMF"](tmf-api.md#following-tmf-2026-10-17), design approved by
the human on 2026-10-18; the ordering side is in
[tmf-api.md](tmf-api.md#modify-items-2026-10-18). The human's decision: a `modify` item
changes a product's offering from `requestedStartDate`, **billed prorated** - the old
offering until the day before, the new one from that day. Migration `0027`.

- **History.** New table `product_offering_change` (`product_id`, `change_date`,
  `product_offering_id`, `changed_by_item_id` UNIQUE -> the modify item,
  `effect_announced_at`; UNIQUE `(product_id, change_date)`). `product.product_offering_id`
  stays the offering the product was created with and never changes. The offering on
  day d is the one from the latest change with `change_date <= d`, else the initial one
  (SQL: `product_offering_on(product, day)`). **Visibility** is the same as for
  terminations: the adapter passes a change only if the order that set it completed
  before 00:00 of the run date, so a replay sees what the daily run saw.
- **Core input.** `Product.changes: tuple[OfferingChange(day, offering_id), ...]`
  (sorted by day, default `()`, so existing callers keep working) and
  `offering_on(day)`. Prices are loaded for every offering a product has had.
- **Segments.** The change days inside a period cut its billable range into segments.
  Each segment is billed the way a whole period is billed today: charged in advance once
  its first day is <= as_of, at the price of *its* offering valid at midnight of *its*
  first day, `round_half_even(price x segment days / period days)`, rounded once per
  segment. A segment has its own price lookup, so a price change of the new offering
  that starts before the change day applies from the change day on. A price change
  within one offering still applies from the next period, as before.
- **Stored ABRs are matched to segments by start day.** A charge must start on a
  segment's first day (else `InconsistentState`), at most one per segment, and its
  offering (new ABR column, below) must be the segment's. The current diff then runs
  per segment: expected minus (charge + refunds); a refund covers
  `[segment end, charge end)`. Examples (cycle day 1, change to B on the 10th):
  - A was charged for the whole month on the 1st, and the change is first seen by a
    later run: a refund of A for `[10th, 1st)` and a charge of B for `[10th, 1st)` (on
    the run of the 10th, or the first run after that day if the change is seen later).
  - The change is known before the month is charged: a charge of A for `[1st, 10th)` on
    the 1st, and a charge of B for `[10th, 1st)` on the 10th.
  - Change on the cycle day: a plain split by period, no proration at all.
- **One refund per charge still holds.** A charge's expected net drops once: when a
  later change or a termination cuts its segment short. The ordering rules prevent a
  second cut: at most one change per product that has not taken effect yet; a delete
  may not end a product on or before a pending change's day; a change must come before
  a known termination. A second reduction is still `InconsistentState`;
  `refunds_rate_id UNIQUE` stays.
  **Gap found 2026-10-19** (`make verify`'s `ci` profile,
  `test_refunds_overlap_precision_currency`): those rules let a change through after a
  termination in the same period was already refunded - charge for the month, delete
  for the 17th (refunded `[17th, 1st)`), then a change from the 13th: a second refund of
  the same charge, `InconsistentState`, the account's billing stopped. Reachable through
  the API since slice 8b (deployed with `d499338`), with any amount. **The human's
  choice: an ordering rule, not a second refund.** A modify item is rejected
  (`terminationInPeriod`) when the product's known termination is before the end of
  D's billing period (a termination on the next cycle day cuts nothing and is allowed);
  cancel the delete order first. The property generator follows the rule
  (`product_specs` takes the cycle day); `tests/test_api_order_modify.py` has the
  sequence end to end with the worker. Negative proof: without the rule both API tests
  fail (`[] == ['terminationInPeriod']`).
- **`abr_no_overlap` is replaced by `abr_one_per_start`** (the human's choice,
  2026-10-18, after the property tests found the approved design's gap): a charge is
  refunded, never shortened, so after a mid-period change the old offering's charge
  `[1st, 1st)` and the new one's `[10th, 1st)` overlap, and so can refunds of two
  segments. The unique index on `(product_id, charge_type, lower(period))` allows one
  charge and one refund per product and first day: it still catches a run writing the
  same ABR twice (the concurrency test's collision without the account lock happens
  there now). The core keeps the rest: charges start on segment days, a refund lies
  inside its charge, one refund per charge, the net per segment is exact. A refund that
  rounds to zero is not written (as before), so a charge's net range may also overlap.
- **ABRs store their offering.** `applied_customer_billing_rate.product_offering_id`
  (NOT NULL, foreign key), back-filled from the product: until now every product has
  had exactly one offering. The core sets it (a refund: its charge's offering) and
  checks a stored charge's against its segment; an insert without one (tests' factories,
  older code paths) gets `product_offering_on(product, first day)` from the trigger
  `abr_default_offering`. Bill charge lists, the account ledger, account charges and the
  TMF678 ABR's product name show the ABR's offering instead of the product's initial
  one, which would name the old offering on the new one's charges.
- **Bills, settlement and taxes are unchanged.** Every segment's charge gets the tax
  lines of its own price, and its refunds work by diff, as in
  [Taxes](#taxes-2026-10-18).
- **Migration `0027`**: the table, `product_offering_on`, the ABR column (added,
  back-filled, then NOT NULL) with its trigger and index, `abr_one_per_start`, and the
  order item checks of [tmf-api.md](tmf-api.md#modify-items-2026-10-18). Rolling back
  needs `alembic downgrade 0026` by hand; it refuses while a change exists (its charges
  would no longer match a single offering) or a modify item that was not rejected.

Tests: the offering-change cases in `tests/test_billing_core.py` (the three examples
above, a change plus a termination in one period, two changes in one period, a change on
the start day, the new offering priced on the change day, one rounding per segment, a
charge of another offering or off a segment day raising); `tests/test_billing_properties.py`
now gives a product one random change under the ordering rules (the oracle splits each
period by offering day by day and rounds once per segment): idempotence, net per
segment = one rounding, lifetime without drift, daily vs one catch-up run, replay,
unique first days; `tests/test_api_order_modify.py` for the adapter and API side;
`test_0027_offering_changes`; `test_one_charge_per_first_day_overlaps_are_allowed`.
Negative proofs: pricing every segment at the period's first day failed eight core and
property tests; a change visible a day early failed
`test_a_change_placed_on_a_day_is_first_seen_by_the_next_days_run`; the TMF678 ABR
naming the product's initial offering failed the prorated-billing API test.

## Open questions

- **Changing `billCycleDay`.** PATCH is a 409 today. Not settled in stage 3: a change has
  to decide how the current period is treated (close it early, stretch it, or only apply
  from the next period). The billing core assumes a fixed cycle day; with a changed one,
  stored ABRs no longer sit inside one cycle period and `compute` raises
  `InconsistentState`.
- ~~**Suspension semantics.**~~ Closed 2026-10-19 ("decide based on TMF", confirmed by
  the human): products are suspended and resumed by TMF622 modify items with
  `product.status` ([tmf-api.md](tmf-api.md#suspension-2026-10-19)), and **billing does
  not change** - no spec says what a suspension does to charges, so `Suspended`/`Closed`
  billing accounts and `suspended` products are billed by their dates like any other. A
  rule that stops or reduces charges would be ours, a business decision for the human.
  Ordering refuses only adding a product or changing its offering on a non-Active account
  (C9).

The resolved items of CLAUDE.md's old "Open questions" moved too: stale billing runs and
`APP_ENV` to [worker.md](worker.md#resolved-questions), the two template ports to
[infra.md](infra.md#to-port-to-vps-lab-template).
