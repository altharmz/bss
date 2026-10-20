# Configurable product charge periods (2026-10-20)

## Requested scope

The human requested daily, weekly, monthly and yearly product charge periods with
positive integer multiples. Account invoice cycles remain monthly. This extends the
monthly-only policy in [catalog.md](catalog.md) and [billing-core.md](billing-core.md).
Deployed as `bss-app:7ad101e` on 2026-10-20; migration `0030` is applied locally and
in production. All local `make verify` checks passed; deployment completed on the
second `make deploy` retry after intermittent public connection timeouts. App, worker
and MCP were healthy, and all web and MCP smoke checks passed on the final attempt.

## TMF contract

The vendored TMF620 v5 specification defines `recurringChargePeriodType` as a free
string describing the repetition unit, and `recurringChargePeriodLength` as an integer
multiplier. It gives month and week as examples, without fixing an enumeration,
calendar anchor, proration policy or leap-year policy. Our supported vocabulary will
be `daily`, `weekly`, `monthly`, `yearly`, preserving the existing `monthly` spelling.
Quarterly is monthly with length 3; every two weeks is weekly with length 2.

Multipliers are integers from 1 to 1000 (our implementation limit). The price is the
amount for the entire selected interval. TMF666's account billing
cycle remains a separate concept: monthly invoices collect charges posted by their
issue date, including charges for periods longer or shorter than one invoice cycle.

## Approved calendar policy

Monthly length-1 subscriptions retain their account cycle day and current
first-period proration. The new cadences use activation-date
anchoring, with a full first interval. A change to a different offering starts the new
offering's schedule on its effective date and refunds the unused old interval.

For month/year anniversaries, compute each boundary from the original anchor and clamp
the day to the last day of the target month. Do not repeatedly add a clamped boundary:
For example, every two months from December 31 gives February 28, April 30 and
eventually August 31. A yearly February 29 anniversary becomes February 28 in a non-leap year
-> February 29 in a leap year. Day/week durations count business dates in BILLING_TZ,
not multiples of 24 hours across DST changes. All intervals remain half-open.

The human approved activation-date anchoring for the new cadences on 2026-10-20.

## Implementation

1. Add a new migration for the supported recurrence units and positive lengths,
   retaining existing data and monthly defaults. Extend TMF620 create, patch, inline
   prices, import/export and repository persistence. Historical prices remain
   immutable once ordered. A successor price keeps the cadence of the offering in
   that currency; changing cadence on a live subscription uses an offering-change
   order, so past period boundaries cannot be reinterpreted.
2. Generalize charge-period calculation separately from invoice cycles. Carry cadence
   through price loading, order quotes and snapshots, inventory price views, the
   billing core, offering changes, next-charge dates, charge-day counts and previews.
   Preserve exact Decimal proration, half-even rounding, tax/refund differences,
   daily replay visibility, idempotence and the one-refund-per-charge ordering rule.
   The latter must use the actual charge period when checking a known termination.
3. Add recurrence unit and multiplier controls to New offering. Show interval-aware
   amounts in catalog, order, subscriber and preview views. Remove monthly assumptions
   from totals: amounts with different intervals must not be added into a misleading
   single recurring total. Pin UI request bodies in component and backend tests.

## Validation

Exercise every unit and multiples, first charge, next charge, partial termination,
offering change across cadences, successor prices, taxes, leap days, month ends and
DST. Prove daily replay and catch-up consistency, no duplicate charges, exact net
after refunds and unchanged legacy monthly results. Validate API persistence and
round trips, migration upgrade/downgrade rules, mixed-cadence UI labels and request
bodies. Run the project backend/frontend checks and negative proofs before reporting
implementation complete. Deployment and commits require the existing human approval.

The implementation retains the original monthly engine for products whose prices are
all monthly length 1. Products with another cadence use per-offering anniversary
schedules, sharing the existing segment charge/refund and tax arithmetic. Stored
charge starts and refund references remain the idempotency keys. Order price views
derive cadence from their immutable quoted catalog price; mixed-cadence orders return
separate recurring totals rather than adding unlike intervals.

Migration `0030` widens the recurrence column to checked text, preserving the original
monthly enum for downgrade, and widens the length constraint. Downgrade refuses while
a non-monthly or length-not-1 price remains; it never rewrites those prices. Existing
monthly prices and historical ABRs are unchanged. The UI and MCP accept the same units
and lengths. Internal preview/product views expose an optional interval label; absent
means the original monthly interval.

Coverage: `tests/test_charge_periods.py`, `tests/test_charge_period_api.py`, the `0030`
migration case, the pinned offering requests in `tests/test_ui_requests.py` and
`Catalog.test.tsx`, and the read-only browser smoke. Negative proofs: removing the
sidebar count failed its shell assertion; treating a week as one day failed the
explicit weekly interval example. Both mutations were reverted.
