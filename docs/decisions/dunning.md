# Postpaid dunning (issue #385, 2026-11-23)

Slice 2 of postpaid for the MVNO (epic #182; slice 1 is the bill PDF and email, #202; the UI is
#386). Decided in the issue on 2026-11-15 (data-model plan approved under the human's
delegation); what the build added or changed is marked **Build decision**. Migration `0090`.

Index: [README.md](README.md). Related: [bills.md](bills.md) (payment term, hold/release),
[prepaid.md](prepaid.md#line-lifecycle-grace-suspension-deactivation-and-number-aging-2026-11-06-issue-188)
(the pattern this follows), [notifications.md](notifications.md), [worker.md](worker.md).

## The rule

**TMF has no dunning API** (TMF678 bills carry `paymentDueDate` and a state only), so the
settings and the views are our own internal API. Everything follows from **O**, the first
business date an account has an open, non-held bill past its `paymentDueDate`: the oldest such
due date + 1. It is recomputed on every pass and nothing stores a state machine, so a settings
change applies to the steps not yet taken.

- **Overdue** = open (`sent`, `partiallyPaid`), positive bills with `payment_due_date` before
  today; the amount is the sum of their remaining amounts (amount due minus allocations). Held
  bills (`onHold`) never count; settled bills and credit notes have nothing remaining.
- **In dunning** = an Active, non-exempt postpaid account with an overdue amount of at least
  `minimumOverdue` (and above zero). Accounts in `Suspended` or `Closed` state are skipped.
- **Steps** (`app/billing/dunning.py`, pure): `reminder1` on O + `reminder1Days`, `reminder2` on
  O + `reminder2Days`, `suspend` on O + `suspendDays`. **Build decision:** only the latest step
  whose date has come is taken, and none once it or a later one was taken. A worker that was
  down, or a settings change that moves the steps, never sends two reminders on one day or a
  reminder after the suspension.
- **Suspension:** every product of the account that is `active`, started and has no end set is
  suspended through an **internal TMF622 order** (modify `status=suspended`, dated today, cause
  `dunning`), so orders alone change products. A product with a status or delete order in flight
  is skipped. The product gets `dunning_suspended = true`.
- **No network command (build decision).** The issue expected network `suspend`/`restore`, but
  postpaid orders send no network command at all: only prepaid lines are provisioned
  ([network.md](network.md)), and a postpaid line was never activated there. The dunning orders
  take the postpaid order path unchanged; if postpaid lines are ever provisioned, the order path
  sends the commands for dunning as for any status order.
- **Deactivation is not automatic** for postpaid: an operator ends lines. The dunning list marks
  accounts suspended more than 60 days as `deactivationCandidate`.
- **Restoration:** once the account is no longer in dunning, the next pass places an internal
  resume order (cause `dunning`) for each `dunning_suspended` product. **Build decision:** "no
  longer in dunning" means it owes less than `minimumOverdue` past due (paid, credited, held), not
  only zero: an amount too small to start dunning does not keep lines suspended either. An exempt
  account is also restored. A product whose status changed today waits for the next business
  date (one status change per product and day, as in the prepaid lifecycle).
- **The flag** `product.dunning_suspended` is set exactly when the product's latest applied status
  change is a suspension with cause `dunning`: by `products.change_status`, by the worker's
  `apply_status_changes` for dated changes, and by `restore_applied_status` after a cancellation.
  So an operator's suspension is never undone by restoration, and an operator's resumption during
  dunning clears the flag; the account is not suspended again for the same O.

## Records

- `dunning_setting` (one row; defaults 1, 7, 14 days and 5.00): `CHECK` keeps the days in
  0..365 and increasing; the API answers 422 before the database would refuse.
- `dunning_step` (PK account, O, step; steps `reminder1`, `reminder2`, `suspend`, `restore`): the
  once-only guard and the durable record. `products` counts the orders the engine accepted for
  `suspend` and `restore`. `restore` is recorded under the O of the account's latest suspension.
  If an account becomes overdue again with the same O after a restoration (a released hold, say),
  that O's steps are already taken and nothing is repeated.
- `product_status_change.cause` takes `dunning`; `billing_account.dunning_exempt`;
  `account_audit.action` takes `dunning_exemption` (detail `{exempt}`, the reason, the operator;
  **build decision:** an operator's account setting, so it is no customer security alert).
- Indexes: `customer_bill_open_due_idx` (open positive bills by due date: the daily sweep) and
  `product_dunning_suspended_idx` (the restoration check on every tick).

## Worker

`app/worker/dunning.py`, in `loop.tick` after billing, the prepaid passes and the entitlement
observation, before the notifications. Per account, under its row lock, in its own transaction.
**Build decision (cost):** the steps follow the business date, so the full pass (every Active
postpaid account with a bill past due or a dunning suspension) runs on the worker's **full sweep**
(the first tick of a business date, or after a settings change: `account_review.settings_key` now
includes the dunning settings). Every other tick looks only at accounts with a dunning-suspended
product, so a payment restores the lines within a minute without scanning bills every minute. A
rejected order is logged; a suspension refused for a product is not retried (the step is taken),
a restoration is tried again on the next pass.

## Notifications

The detector `dunning` (`app/notify/events.py`) reads `dunning_step` rows taken within the
lookback: `dunning_reminder1`, `dunning_reminder2` and `dunning_suspended` (only when the
suspension suspended at least one product), dedupe key `dunning:{account}:{O}:{step}`,
transactional, by email and SMS (an account event: SMS to a phone contact, else the account's
first line). A reminder names the amount past due and the suspension date the settings give now.
Restoration sends nothing.

## API and MCP

Internal API (`app/internal/dunning.py`, `spec/internal/bss-internal.oas.yaml`), each an MCP tool:

- `GET /internal/v1/dunning/settings`, `PATCH` (**build decision:** PATCH, not the issue's PUT:
  every field is optional, and the MCP connector generates write tools for POST and PATCH only).
  `minimumOverdue` is a JSON number in the account currency, at most 2 decimals; null is a 422.
- `GET /internal/v1/dunning/accounts?step=none|reminder1|reminder2|suspend&deactivationCandidate=true`,
  paged (Link `rel=next`): O, the amount past due, the last step for that O, the suspension date,
  `deactivationCandidate`. Oldest O first.
- `GET /internal/v1/billingAccount/{id}/dunning`: **build decision:** there is no subscriber detail
  endpoint (the subscriber card reads several), so the account's dunning state is its own read:
  `exempt`, `inDunning`, O, the amount past due, `stepsTaken`, `nextStep` (dated today or earlier
  while the worker has not run yet), `suspendedProducts`. A prepaid account is never in dunning.
- `PATCH /internal/v1/billingAccount/{id}/dunning` `{exempt, reason?}`: postpaid only (422), an
  audit row on a change, none when the value is the same.

The UI is #386 ([frontend.md](frontend.md#bill-pdfs-delivery-and-dunning-ui-2026-11-24-issue-386)): the Dunning screen, the Billing settings tab and the card's panel call all five, so they left the parity allowlist.

## Tests

`tests/test_dunning.py`: the pure step function at its boundaries; the timeline under the
injected clock (due, reminder 1, reminder 2, suspension through a completed order with cause
`dunning`, the customer told each step once on both channels, payment, restoration on a tick that
is no full sweep), each step once over two passes; the wiring through `loop.tick`; payment on the
suspension day; a held bill; an amount under the minimum; a partial payment below the minimum
restoring; an exempt account (no step, suspension lifted, audit, no alert); an operator's
suspension next to a dunning one (only the latter restored); no suspension notice when nothing
was suspended; an operator's resumption; Suspended accounts skipped; a settings change mid-way;
settings validation; the list with its filters, paging and candidates; prepaid and unknown
accounts. Negative proof, made once and reverted: restoration without the `dunning_suspended`
check (the operator test failed). **TEST-CHANGE:** the table count is 101 (`test_migrations.py`,
`test_nonsuperuser_migration.py`); the MCP tests know the five new tools.
