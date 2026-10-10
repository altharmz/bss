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
