# Subscriber numbers and prepaid: finalized design (2026-10-20)

## Prepaid MVNO on T-Mobile: scope and integration pattern (2026-11-05, epic #182)

Scoped with the human on 2026-11-05; the slices #183-#194 record their own designs in the
area files ([network](network.md), [taxes](taxes.md), [payments](payments.md), [sim](sim.md),
[numbers](numbers.md), [usage](usage.md), [notifications](notifications.md),
[accounts](accounts.md#account-security-pin-verification-locks-and-audit-issue-193-epic-182)
and the sections below). The epic closed on 2026-11-06 with the backend built; the admin UI
halves (#205-#226) stayed open as their own issues.

- **Direct T-Mobile connection**, no MVNE. Network provisioning is a placeholder that assumes
  the network answers correctly.
- **Prepaid is the focus**, plan-based (bought up front, renewed from the balance or a saved
  card) and pay-as-you-go (usage rated against the money balance). Postpaid keeps working
  but is not extended.
- **Caps and throttling are enforced by T-Mobile.** The BSS sends the plan's network profile
  and receives usage records; there is no real-time online charging in the BSS.
- **No vouchers or PIN cards**, no retail top-up networks. **Self-care is set aside**: the
  scope is the BSS, the admin UI and the APIs.
- **End of plan without funds:** grace, suspension, deactivation, then the number is aged
  and returns to the pool; the durations are settings (#188, below).
- **TMF where it covers the area** (spec vendored into `spec/`, pinned in `SHA256SUMS`):
  TMF635, TMF639, TMF654, TMF670, TMF681. An internal API where it does not (porting,
  lifecycle settings, the tax and gateway adapters).

**Placeholder pattern for every external system** (T-Mobile, tax engine, payment gateway,
SMS/email):

- One package per system under `app/integrations/<name>/` with a typed interface and a stub.
  `BSS_<NAME>_ADAPTER` selects it at startup (default `stub`, `log` for notify); an unknown
  value fails startup.
- A call that changes something outside the BSS is a durable outbound command (state,
  idempotency key, attempts, last error) sent and retried by the worker, never inside a
  request transaction. Results come back asynchronously and are recorded; the stub confirms
  on the next worker wake-up.
- The stub is deterministic and has documented failing test values (an MSISDN, a card
  token), so the failure paths are tested.
- A real integration is its own issue (#203); its credentials are created by the human and
  live in `.env.prod`, never in the repository.

**Out of scope** (ideas kept as issues): vouchers and PIN cards; self-care and online signup
(#195); real-time online charging; extending postpaid (#202); real T-Mobile, tax, payment and
messaging connections (#203); family plans (#196), wholesale reconciliation (#197),
reporting (#198), care cases (#199), international and roaming add-ons (#200), E911
address (#201).

## Line lifecycle: grace, suspension, deactivation and number aging (2026-11-06, issue #188)

Epic #182. Design approved on the issue (the human's standing delegation, decided following
TMF; TMF has no lifecycle-policy API, so the settings are internal). The UI is issue 188b.
Migration `0059`. This is the hand-over #187 left: a renewal nobody can pay stays pending
and the product is funding-suspended; the lifecycle now takes it from there.

**Everything derives from the first unfunded day D and the settings** (`app/billing/lifecycle.py`,
no stored state machine), so a settings change applies to every transition not yet taken and
transitions already taken stand:

| Phase | Dates | Service | Taken as |
|---|---|---|---|
| grace | D .. D+grace-1 | on, nothing is sent | nothing (the product already shows `suspended`, cause `funding`: no paid interval) |
| suspension | D+grace .. D+grace+suspension-1 | suspended on the network | internal order, modify `product.status=suspended` -> network `suspend` |
| deactivation | D+grace+suspension | ended | internal order, delete item dated today -> network `deactivate`; the number is aged |

For a **plan** D is the end of the latest funded interval when it is not after today. For a
**pay-as-you-go** line D is `balance_zero_since` + `payg_zero_balance_days`; the worker keeps
`billing_account.balance_zero_since` (the first business date of the current run of a zero
spendable balance, cleared when the balance is above zero). Add-ons are not lifecycle subjects:
they end with their plan.

**Settings** (`prepaid_lifecycle_setting`, one row; `GET`/`PUT /internal/v1/lifecycle/settings`,
each field optional on PUT): `graceDays` 3 (0..365), `suspensionDays` 30 (0..3650),
`numberAgingDays` 90 (0..3650), `paygZeroBalanceDays` 7 (0..365). The defaults apply from the
migration. The number-aging value is snapshotted into the claim when the line is deactivated.

**The worker pass** (`app/worker/lifecycle.py`, every tick after the renewals are produced and
before commands are dispatched): per prepaid Active account, under its lock, per open
non-add-on product: skip an operator's suspension (`manually_suspended` without
`lifecycle_suspended`: an operator pause is never ended by the policy), skip a product with a
status change pending or an open delete item (an order in flight), then take one transition:
`suspend` (unfunded, today >= D+grace, not suspended), `deactivate` (unfunded, today >=
D+grace+suspension; also straight from grace if the dates are already past), `resume`
(lifecycle-suspended and funded again). Each is a TMF622 order through `place_order` (an
`ItemRequest(lifecycle=True)`), so orders alone change products, the network adapter sends
`suspend`/`restore`/`deactivate` and the item completes when the network confirms. One
attempt per product, action and business date (`prepaid_lifecycle_event`, UNIQUE): a refused
or rejected order is tried again the next business date, never on every tick.

**A lifecycle suspension does not block renewal.** `product_status_change.cause` is `manual`
(default) or `lifecycle`; `product.lifecycle_suspended` is a subset of `manually_suspended`
(a CHECK), set and cleared with it. The renewal producer, the renewal blocker and renewal
posting treat only `manually_suspended AND NOT lifecycle_suspended` as a block. So the pending
renewal keeps being retried on every credit signal and the card auto-pay rule (#187) keeps
charging once per business date during grace and suspension; when the renewal posts, the
next tick places the `resume` order (network `restore`). Entitlement shows `suspended` with
cause `lifecycle` until the network has restored the line, then `active`. A top-up during grace
only renews (nothing was suspended, nothing is sent).

**One status change per product and day** stays the rule: a resume that finds the suspension
dated today is itself dated the next business date (a top-up on the day of the suspension
restores the line the following day). An operator who resumes a lifecycle suspension by hand
without funds is re-suspended by the worker (it is still unfunded and past its grace); the
remedy is a top-up. Manual suspend/resume orders and everything else about them are unchanged.

**Number aging reuses the TMF639 quarantine state** (`allocationStatus: quarantined`, derived
from the claim: `released_on` + days). A claim now has `aging_days` (0 by default, set when the
line's deactivation order releases its MSISDN claim); the effective quarantine is
`max(MSISDN_QUARANTINE_DAYS, aging_days)` in the number state, the inventory listing and the
assignment check, so the number is not assignable before the aging ends and is on that date,
with no worker step. A cancelled deactivation (refused by the network) restores the claim and
resets `aging_days`. A **port-out is released and locked, not aged**: it ends the product
through its own order, not the lifecycle.

**Internal API.** `GET /internal/v1/billingAccount/{id}/products` (prepaid products) gains
`lifecycle` `{state: active|grace|suspended, nextTransition: {action: suspend|deactivate,
date}|null}` (null for an operator-suspended line and for add-ons) and `suspensionCause` may be
`lifecycle`. No MCP tool (the settings are an operator screen: scope.yaml excluded).

**Not in this slice:** the UI (188b: state and next transition on the product card, the
settings page), carrier-side number release. The customer notifications of each phase are done
(#192, [notifications.md](notifications.md)).

**Tests:** `tests/test_prepaid_lifecycle.py` (dates and pure schedule; unfunded renewal through
grace, suspension and deactivation with the commands the network got; top-up in grace and in
suspension; card payment in suspension; same-day suspension and top-up; number aging before
and after the end; settings API and settings changes on transitions not yet taken; refused
suspension retried next date; operator suspension untouched; pay-as-you-go), plus the
port-out assertion in `tests/test_number_porting.py`.

## Plans, renewal, add-ons and fees (2026-11-05, issue #187)

Epic #182. The design was approved on the issue (the human's standing delegation, decided
following TMF); the UI is issue 187b. Migration `0057`. Builds on the network adapter
([network.md](network.md)), the tax adapter ([taxes.md](taxes.md)) and the payment adapter
([payments.md](payments.md)).

**Offering kind (no new column).** A TMF620 `productOfferingCharacteristic` named
`offeringKind` with one `characteristicValueSpecification` value: `plan` (the default when
absent), `addOn` or `payAsYouGo` (`app/network/kind.py`; any other value is a 400). Only a
plan has a recurring price: attaching a recurring price to an add-on or pay-as-you-go
offering, or making an offering with one of those kinds, is a 422. A plan's period is its
recurring price's period (monthly, or `daily` with a length for a fixed number of days).

**One-time fees.** A `oneTime` price of the offering, valid at midnight of the start day, in the
account's currency and above zero, is a fee (the catalog allows one valid `oneTime` price per
offering and currency at a time). It is quoted at acceptance with the first interval (taxed
like any charge, the engine as `one_time`, else the price's flat tax), included in the hold
(`IntervalQuote.fees`, `gross`), and posted by the billing owner as one `oneTime` ABR per fee
(`one_time_price_id`, `charge_name` = the price name) on the same bill as the interval, one
day long. The fee is not part of the funded interval (`funded_interval.amount/tax_amount` stay
the recurring charge, which is what a termination credit refunds); renewals, offering changes
and mid-period changes charge no fee. A future-dated purchase quotes its fee at posting. TMF678
shows it as an `appliedBillingCharge` named after the price; the account's charges, the
transaction ledger and the bill document use the same name. Before this migration a `oneTime`
rate belonged only to a transfer (no product).

**Renewal from the balance** is unchanged (P3e). **Renewal from the card:** auto-pay is the
account's auto top-up setting (`auto_topup_enabled`) with a usable default card
(payments.md). On the wake-up after a renewal's shortfall was recorded, the worker makes one
`payment_command` of purpose `renewal` for what the renewal still lacks (its required amount
less the spendable balance, read under the account lock), one in flight per account (partial
unique index). When the gateway confirms, the payment and TMF654 top-up are recorded and the
renewal posts in the same wake-up (the payment is the credit signal of the existing retry
rule). A declined or exhausted charge is not repeated until the renewal's next shortfall
observation (the daily rescan), so a bad card is tried once per business date. The charge is
linked to the renewal (`renewal_command_id`); the renewal history shows `fundedBy: card`.
**No card, no auto-pay or a card that cannot be charged:** nothing is charged; the renewal stays
pending with its shortfall (`needs_topup`), entitlement already ends at paid-through and the
product is `suspended` with cause `funding`. That is the hand-over to the lifecycle slice
(#188, [below](#line-lifecycle-grace-suspension-deactivation-and-number-aging-2026-11-06-issue-188)),
which adds the grace period, suspension and deactivation on top of this state.

**Add-on packs.** A TMF622 order whose `add` items are add-on offerings, each with
`product.productRelationship` `[{relationshipType: "addOn", id: <plan product>}]` (TMF622/637
`ProductRelationship`; accepted on add items only). Rules, all TMF622 error codes on the
order: `addOnParentRequired`, `addOnParentUnknown` (not this account's), `addOnParentNotPlan`,
`addOnParentNotActive` (no paid period running today or the plan is suspended),
`noOneTimePrice`, `startNotToday` (add-ons and pay-as-you-go lines start today),
`mixedOfferingKinds` (plans, add-ons and pay-as-you-go lines are separate orders),
`prepaidOnly`. The add-on is a product (`parent_product_id`, `grantless`) created with
`termination_date` = the plan's paid-through date (earlier if the plan is ending), so it
ends with the period; it has no funded interval and is never renewed; terminating the plan
ends its add-ons (`post_termination`). The charge is its fees: held at acceptance, posted by
the billing owner (`app/billing/one_time.py`, command request `{order_id, charges}`) after
the network confirmed. The network gets one `change_profile` on the plan's line: the plan's
profile plus the pack's allowance (`profile.combine`: data, cap, minutes and messages added,
flags switched on, throttle speed kept). The line goes back to the plan's profile with the
next period (the network applies a period's allowance per period; no revert command is sent).
A refusal releases the hold and cancels the add-on, as any refused purchase.

**Pay-as-you-go.** `offeringKind payAsYouGo`: no recurring price. The order activates the line
(network `activate`), charges its one-time fee, and the product is `grantless`: active from its
start, never renewed (no grant). Suspension and termination orders work on it
(`_not_started` skips grantless products). Usage rating and barring at zero balance: [usage.md](usage.md#pay-as-you-go-rating-issue-191-epic-182).

**Entitlement of a grantless product** (`entitlement.effective_state`): manual and account
suspension apply as usual; otherwise `pendingActive` until the order completes, then
`active`, and `terminated` at its termination date.

**Internal API** (`GET /internal/v1/billingAccount/{id}/products`, prepaid products): `offeringKind`,
`parentProduct`, `fundingSource` (`balance` or `balanceThenCard`) and `renewalHistory` (period,
amount with tax, kind purchase/renewal/offeringChange, `fundedBy`, bill). No new TMF or MCP
operation.

**The admin UI (2026-11-07, issue #212).** The subscriber's product row shows the offering kind,
the funding source and, for an add-on, its plan and end date; a toggle opens the renewal
history. *Buy add-on…* is offered on an active plan with a paid period running and places the
TMF622 add order above (start today); a refusal shows the mapped order error. The catalog edit
sets the kind (a plan carries no characteristic; the other characteristics are kept as
loaded) and the offering detail lists the one-time prices. **`firstCharge` change (additive):**
for a prepaid account it lists the one-time `fees` (name, amount, tax, gross), and `required`
and `shortfall` include them, as the hold does; an add-on or pay-as-you-go offering has no
recurring price, so its quote is its fees alone (price zero, a one-day period, a 422 when it
has no fee either). Pay-as-you-go lines and the add-on offerings are not offered in Add
product: a pay-as-you-go purchase UI is not part of this slice.

**Deployment.** Migration `0057`; existing prepaid products are untouched (`grantless` false,
their grants and paid-through dates stay; renewal by card applies from their next renewal).

## Daily retry of unfunded renewals (2026-11-15, issue #340)

On the 100k load-test dataset 24,874 renewals were unfunded, and the wake-up after midnight
spent 81 minutes retrying them one account at a time (load, lock, attempt; about 6 a second)
while billing requests, the usage feed and notifications waited. The retry now has two paths.

**Signalled renewals go through the per-account path, unchanged.** A pending command is
signalled when it was never attempted since its latest shortfall, its product ends, or a credit
reached its account after that shortfall: a done payment (a top-up records one), a released
hold, or a **funding source** with a later `available_at`. The funding source is the account
credit signal added here: an adjustment credit, an incoming transfer and a debit's reversal each
make one, so these credits retry the renewal in the same wake-up, not on the next date.

**A renewal due again only because the business date changed is decided in bulk.** The
dispatcher (`_record_date_only_shortfalls` in app/worker/prepaid.py) reads those renewals
(`prepaid_commands.date_only_retries`: pending renewals, no signal, product and account able to
post, no unanswered network profile change). It then reads their accounts' spendable balance in
one statement (`funding.spendable_many`: the ledger less outstanding holds, equal to
`funding.load(...).spendable`, without the account lock or the reconciliation check). A renewal
still short of its latest `required` gets today's observation in one `INSERT ... SELECT`
(`prepaid_commands.record_daily_shortfalls`), with `required` carried over and `available` = the
spendable balance (zero when negative). Nothing is locked, loaded or posted for it. Card auto-pay
still charges once per observation, so once per business date. A covered renewal, and any row the
insert refuses (the table's uniqueness on `(command_id, observed_at)` or its
`required > available >= 0` rule), stays due and is attempted per account like a signalled one.
The observation instant is read before the balances, so a payment received after it still
counts as a credit signal.

**Accepted limit: a changed price.** The carried-over `required` is out of date only when the
offering's price or tax changed since the last attempt. Such a renewal is attempted on its next
credit signal, or on the first date the balance covers the old `required`. Before #340 the daily
rescan was also what picked up price changes (P3e below).

**Time budget.** The per-account loop of `process_commands` stops after `COMMAND_BUDGET`
(60 s, as `FEED_BUDGET` and housekeeping's `TICK_BUDGET` do). The check runs between accounts,
never inside an account's transaction, and at least one account is always attempted. Accounts
with a signalled command go first. Commands left over keep their eligibility and the next wake-up
takes them; a covered date-only renewal is found covered again then.

## Roaming and international passes (2026-11-16, issue #200)

Epic #182; the UI is #383. Design approved on the issue (the human's delegation, decided
following TMF). Migration `0084`.

A pass is an add-on pack (above, "Add-on packs") whose specification has the new roaming or
international profile characteristics ([network.md](network.md)); its usage is tracked per zone
([usage.md](usage.md#usage-zones-2026-11-16-issue-200)).

- **Pass duration:** the offering characteristic `passDays` (one whole number from 1 to 30, on
  an add-on only; 400 otherwise). The pass ends after them or with the plan period, whichever is
  first: `termination_date = min(start + passDays, plan paid-through)`. Without it an add-on ends
  with the period, as before.
- **The profile sent** at a purchase is the plan's profile combined with the add-ons already
  live that day and the new pack. Before this slice it was the plan and the new pack only, so a
  second pack dropped the first from the network profile.
- **End of a pass:** a pass that ends before its plan period ends gets **one** `change_profile`
  on its end date, with the plan's profile and the add-ons still live then
  (`network_command.purpose = passEnd`, one per item beside its order command; the worker makes
  it in the network step of the wake-up and sends it the same wake-up). It is the only revert
  command: an add-on ending with the period needs none (the next period's profile applies), a
  plan ended early ends its add-ons as before.
- **Unchanged:** add-ons end with their plan, terminating the plan ends them, their fees are
  charged once.

## E911 address of Wi-Fi calling lines (2026-11-18, issue #201)

A prepaid line whose profile has `wifiCalling = 1` has a registered E911 address: given on the
add item (`product.place`, role `e911Address`), else copied from the account's service address,
else the order is rejected `e911AddressRequired`. It is changed by a modify item of its own,
sent to the network as `update_e911`, and taken only on confirmation. The rules are in
[network.md](network.md#e911-address-issue-201).

## Multi-line discounts (2026-11-16, issue #196)

Slice 1 of multi-line prepaid (epic #182); pooled data is #379
([usage.md](usage.md#pooled-data-2026-11-20-issue-379)), the UI #380. Design approved on
the issue (the human's delegation, decided following TMF). Migration `0082`.

**A shared balance needs no work:** a prepaid account has one money balance and every line on
it renews from it (P1, P2).

**A tier is a TMF620 `discount` price of a plan offering** with `unitOfMeasure {amount: N,
units: "lines"}`: `price` is the amount off per line and period, N (a whole number, 2 or more)
the minimum number of live plan lines on the account, and `recurringChargePeriodType` the plan's
(required). The stored column `product_offering_price.alteration_lines` holds N; tiers of an
offering and currency may coexist but not overlap per N (the no-overlap exclusion takes N, a 409
like any overlap). Refused (422): N below 2 or not whole, a tier without a period or with
another period than the plan's recurring price, a tier on an add-on or pay-as-you-go offering
(also turning a plan with tiers into one), and a discount of a plan offering whose unit is not
`lines`. A discount **without** a unit of measure stays a record on any offering, as before
(catalog.md). A tier's type and unit never change (PATCH: 422, 409): attach a new price.

**Line count:** the account's plan products live on the quote's start date: `start_date` on or
before it, no `termination_date` on or before it, not cancelled or aborted, in any offering.
Grantless products (add-ons and pay-as-you-go lines) do not count; suspended lines do (they
hold a number). A purchase counts the lines of its own order too, which do not exist yet when
it is quoted, so the first-charge preview, the hold and the posted charge agree; one order of two
lines prices both at the 2-line tier. The tier is the valid one (at the start date's midnight,
in the account's currency, for the plan's period) with the largest N <= the count, on the
plan's own offering (`app/billing/multi_line.py`).

**When:** every interval quote - a purchase (immediate or future-dated, quoted at posting), a
renewal, a mid-period offering change (the changing line counts itself). The quote is fixed for
its period: a line added or ended changes the price from each line's next renewal; nothing is
credited back.

**Money (Decimal):** `IntervalQuote.discount` (price id, amount) and `IntervalQuote.amount` =
recurring − discount, at least 0 (a discount never exceeds the price). Taxes are on the net, from
the engine or the flat rate alike. The hold is the net with tax, `funded_interval.amount` is the
net, so a termination credit refunds the net. The ABR's `amount` is the net and it keeps
`discount_price_id` and `discount_amount` (0 without a tier, CHECK both or neither). TMF678 has no
price alteration on a rate: the rate's `description` ends with "multi-line discount −$5.00
(productOfferingPrice <id>)", and the bill document's line reads "Plan X (multi-line discount
−$5.00)". `firstCharge` (prepaid) adds `discount {amount, productOfferingPrice}`; `price` stays the
full price, `required` and `amount` are net. Quotes stored before this change have no discount
and are read as such.

**Postpaid** is unchanged: it charges recurring prices only.

**Tests:** `tests/test_multi_line.py` - purchases and renewals of 1 to 4 lines with tiers 2 -> 5
and 3 -> 10 (net and tax exact), no repricing of a running period, an ended line stops counting
and is credited its net, the hold equals the posted charge, one order of two lines, the tax
engine on the net, TMF678, the bill document and `firstCharge`, catalog validation and attach
rules, a plain discount stays a record.

## Mid-period offering change (2026-11-04, issue #151)

A prepaid modify item with `productOffering` may now request any date `D` with
`today <= D <= paidThrough` of the product's latest grant (P3h kept only the renewal).
`D == paidThrough` is the P3h path, unchanged. A funding-suspended product (paid-through
passed) changes `today` only; `D` after paid-through is `offeringChangeNotAtRenewal`
("after the current paid-through date"). No first grant is `productNotStarted`, a set
termination `productAlreadyEnded`. One change may wait per product: another is
`offeringChangePending` (before the generic `openModifyItem`).

**Acceptance (`today <= D < paidThrough`).** The new offering's first interval from `D`
is quoted in full, tax-inclusive, with the account's exemptions, anchored at `D`, and the
whole amount is held from current spendable funds, never the anticipated credit (the rule
of multi-item purchases). A shortfall is a stored `insufficientFunds` with nothing held.
Otherwise one `order` command due `D` carries the exact-source hold and
`{"order_id", "offering_changes": [interval quotes]}`; the change is retained in
`product_offering_change` and the order and item stay `inProgress`. Items of the same
order changing at their renewal keep the P3h retained change and complete with the order.

**Execution on `D` (`app/billing/offering_change.py`, one transaction).** For each
product: (1) the old grant's unused dates `[D, paidThrough)` are credited exactly as a P3g
termination credit (`termination.credit_grant`: net for the used dates rounded once,
credit = that minus the charge, tax at the charge's rates, a `refund` ABR of the command
run with a settled `offCycle` credit note, spendable after posting; `D` equal to the grant
start credits the whole interval); (2) the hold converts and the new interval is posted
like a purchase, as a grant that **supersedes** the old one; (3) the retained change gets
`effect_announced_at` and TMF637 AttributeValueChange, the items and the order complete
(TMF622 StateChange). A late worker still credits from `D`. Any failure rolls everything
back; the hold and the pending tree stay for retry; replay posts nothing twice. A
`realizingResource` in the same item moves the number on `D` as at a renewal.

**Cancellation.** Before `D`, `cancelProductOrder` releases the hold, removes the retained
changes and completes the command `cancelled`. On or after `D`: 409 (change the offering
again with a new order). Upgrade and downgrade are the same flow. Postpaid is unchanged.

**Schema (migration `0050`).** The issue expected no schema change; the new grant
`[D, D + interval)` overlaps the old `[start, paidThrough)`, which three rules refused.
`funded_interval.supersedes_interval_id` (nullable, unique, FK with the product) marks the
grant that replaces another. The no-overlap exclusion and the
`(product_id, anchor_date, interval_index)` uniqueness now apply to grants that supersede
nothing, so a superseding grant overlaps (or restarts the anchor of) only what it replaces;
the app checks that it replaces the grant covering `D`. The ABR uniqueness
`abr_one_per_start` becomes `(product_id, charge_type, period, product_offering_id)`, since
a change dated the old grant's first day charges the new offering from the same date.
Known limit: changing back to the same offering for the same period on the same day
(A to B to A on the grant's first day) is refused at posting by that index. The downgrade
refuses while superseding grants or charges sharing a first day exist.

"Latest grant" is now ordered by start then `authorized_at` (not by end), because a
downgrade to a shorter plan ends before the grant it supersedes.

**Preview and UI.** Internal `GET /billingAccount/{id}/firstCharge` takes an optional
`product.id`; with it `credit` (tax-inclusive, zero at or after paid-through) is returned
and `balanceAfter` is ledger minus the held amount plus the credit. The product's Change
offering dialog on a prepaid product offers a date from today to paid-through (default
paid-through) and shows the credit, the amount held now and the balance after. The write
body is unchanged and pinned in `tests/test_ui_requests.py` and `SubscriberCard.test.tsx`.

## Slice 8: dated prepaid status changes and number-only changes (2026-10-25)

Built as designed, no migration, no contract or MCP change (`create_order` never stated a
today-only rule).
- **Acceptance** (`app/ordering/prepaid.py`): a prepaid `modify` item with `product.status` dated
  after today is accepted. The order and items stay `inProgress`, the `product_status_change` row
  is kept unapplied (so `openModifyItem`, `statusUnchanged` against the latest pending change,
  `productAlreadyEnded` and slice 4's `pendingChanges` come from the shared postpaid rules) and one
  `order` command is due on the date, request `{order_id, status_changes: [{product_id, status}]}`.
- **Posting** (`app/billing/status_change.py`, dispatched in `app/worker/prepaid.py`, which locks the
  renewal identities of resumed products before the account): revalidates, then runs P3f's same-day
  execution. Resume after expiry queues the renewal. If the product ended or the account is not
  Active for a resume, items and order end `failed` (first use of TMF622 `failed`: `states.py`,
  tmf-api.md), the error messages carry `productAlreadyEnded` / `accountNotActive`, and the retained
  change is deleted. The command completes with `{outcome: failed|statusChanged}`.
- **cancelProductOrder:** before the command ran it cancels the command, the change and the
  order (`cancel._cancel_status`); once applied, the postpaid same-day rule (allowed on the date,
  409 later); a `failed` order is 409.
- **Number-only** modify items on prepaid run through the postpaid `_execute`: synchronous, no
  command, interval from the date. Mixed with purchase, delete or status items stay
  `prepaidLifecycleUnavailable`.
- **UI:** the prepaid Suspend/Resume date is editable
  ([frontend.md](frontend.md#slice-4-product-actions-and-pending-changes-2026-10-25)).
- **Deviations:** none. Scope item `worker.prepaidDatedStatus`.
- **Tests:** `tests/test_prepaid_dated_status.py` (dated suspend and resume after expiry, cancel
  before and on/after the date, ordering rules, both revalidation failures, number-only today and
  dated, mixed refused), `test_ui_requests.py::test_prepaid_dated_suspend_as_the_ui_sends_it`, the
  component test in `SubscriberCard.test.tsx`. TEST-CHANGE: `test_order_states.py` allows
  `inProgress -> failed`; the prepaid status test no longer expects a dated change to be refused;
  the Resume dialog date is no longer read-only.

## Slice 4: `pendingChanges` on the internal products (2026-10-25)

`GET /internal/v1/billingAccount/{id}/products` returns `pendingChanges` on every product
(`[]` when none; contract in `spec/internal/bss-internal.oas.yaml`, `products.pending_changes`),
ordered by date: `status` entries are unapplied `product_status_change` rows dated after today;
`offering` entries are `product_offering_change` rows not in effect: postpaid dated after today,
prepaid not yet announced (a change waiting for a funded renewal can be dated in the past). Each
names the order to cancel. No migration; the MCP `open_products` carries it. Pending dated prepaid status changes appear
through their unapplied rows (slice 8). Tests: `tests/test_internal_pending_changes.py`.
UI: [frontend.md](frontend.md#slice-4-product-actions-and-pending-changes-2026-10-25).

## Slice 2: transactions include balance actions (2026-10-24)

Bug fix, no migration; built as designed. On a prepaid account the internal `transactions` list (and its count) now
includes the four ledger entries of `BALANCE_ACTION_LEDGER`: `adjustment` (completed or
cancelled, signed, note = reason else "Adjustment"), `reversal` (note "Cancelled top-up" or
"Cancelled adjustment"), `transferIn` ("From {sender}") and `transferOut` ("To {receiver}"),
so the newest `balanceAfter` equals `account_balance`. They are guarded by
`has_balance_ledger`. Fees are already ABRs and a top-up is its payment; holds stay out.
`Transaction.type` in the internal contract gained the four values; a new entry's `href` is
its `balanceActionHistory` resource. A reversal shares its action's `id`, so a list can hold
two entries with one id (the UI keys rows by type and id). Test:
`tests/test_internal_transactions_prepaid.py` (top-up purchase, credit, debit, cancelled
adjustment, transfer out with a fee, transfer in; counts, notes, newest balance equals the
ledger on both accounts; a postpaid list is unchanged).

## P4c: balance read API (2026-10-23)

TMF654 now serves `bucket` and `accumulatedBalance` list/retrieve (`app/api/balance.py`,
`app/repository/buckets.py`) and POST/DELETE `/hub`, with six MCP tools. A bucket is a
view of the `prepaid_bucket` identity: `remainingValue` = L - H, `reservedValue` = H,
both quantized cents in the account currency; L is the TMF666 `currentBalance` query
(now shared as `billing.PAYMENTS_LESS_CHARGES` + `BALANCE_ACTION_LEDGER`) and H the
unreleased hold sources, read in one statement without the account lock: a GET neither
locks nor reconciles (so it cannot fail like `funding.load` would), and a test checks
the figures equal `funding.load`'s spendable/reserved on a ledger with top-ups, credit,
debit, transfer, reversal and a hold. L - H is not clamped, so a debt stays visible.
Status follows the account (Active/Suspended/Closed -> active/suspended/expired).
`product[]` uses the close guard's "not yet ended" predicate; `logicalResource[]` is one
number per such product (today's assignment, else its next accepted claim, so a
scheduled number change does not count twice); `isShared` means more than one number.
`partyAccount` carries the account's description and state, `relatedParty` its customer.
The v4 `accumulatedBalance.logicalResource` is a single reference, given only when
exactly one number is funded. Reads never create a bucket; account creation (P5a) and
the PATCH to prepaid write `prepaid_bucket`.

Accepted deviations (gap ledger): a bucket is never a deduction, so no
`requestedDate`/`confirmationDate`; monetary credit does not expire, so `validFor` has
no end. The hub is a v4 EventSubscription; `EVENT_TYPES` now lists the twelve action
events so subscriptions can name them, while P4d-P4i emit them. The gap probe tracks
pending operations per resource (`tests/tmf_gaps.py` PENDING: 22 action/history
endpoints), detects Swagger 2 request schemas by document instead of by TMF676, and its
nested probe populates a prepaid bucket with a payment and a hold through the database.

## P4b: Idempotency-Key journal (2026-10-23)

Migration `0046` adds `idempotency_key`, keyed by operator (user or MCP principal),
method, route and key (1-255 visible ASCII, else 400). It stores the SHA-256 of the
normalized query and body (key order and number spelling do not matter), the success
status, response body and Location. `app/repository/idempotency.py`: `begin` takes a
transaction advisory lock on the key and returns the stored response, 409 for another
request under the same key; `store` writes in the request's own transaction, so a
rollback or any 4xx/5xx leaves no key and concurrent duplicates queue, then replay.
Only committed 2xx responses are kept (a stored `rejected` order is a 201 and replays).
Rows are retained; the single allowed change is a one-time `tombstoned_at`, set by the
delete of the resource named in the stored Location, after which the key answers 409
forever. **Since #282 (2026-11-11)** the worker's housekeeping deletes a key 30 days after
`created_at`, tombstoned or not: a replay after 30 days is a new request
([data-model.md](data-model.md#retention-2026-11-11-282)). The header is optional and wired to `POST /productOrder` (any account mode);
the balance-action POSTs reuse the helper in P4d-P4g. Downgrade refuses retained keys.
Replays emit no events and post nothing; the internal command identity still guards posting.

## P4d: top-ups (2026-10-23)

`topupBalance` has all five operations (`app/api/topup.py`, `app/repository/balance_actions.py`),
no migration. POST locks the bucket's account (as a payment does), records the payment
through `payment.post_payment` (insert, settle, TMF676 Create) and then the completed
`balance_action` that links it, so a top-up and a direct payment share one money path.
`received_at` is the injected clock's, which is the credit signal the worker already scans
(a done payment later than the shortfall), so a top-up resumes a funding-suspended renewal
on the next tick with no worker change.

A direct done payment on a prepaid account that has a bucket records exactly one completed
top-up (request synthesized, description naming the payment) and sends TopupBalanceCreate;
a prepaid account without a bucket (only test fixtures: P5a creates it with the account) records none. `post_payment`
takes `record_topup=False` from the top-up API itself, which makes its own action: no
loop. Both POSTs take an Idempotency-Key (`idempotency.header`/`replayed` were added next to
the P4b helpers); DELETE of a failed top-up tombstones the key.

Refusals. A reference that cannot be stored or contradicts another is a 4xx with nothing
stored: unknown bucket/product/number, a `partyAccount` that does not own the bucket, units
other than the bucket's currency, non-monetary usage, automatic/voucher fields, amounts
that are not positive exact cents. A valid reference that is not eligible is stored `failed`
with 201 and a Failure event: a Closed account, a product that is not live on the account,
a number it does not hold. Suspended accounts may be topped up. TopupBalance has no field
for the refusal beyond `reason`, so `reason` is the client's when it sent one, else
`failure_reason`; the stored `reason` column is never overwritten.

Cancellation (PATCH `status: cancelled`, only completed -> cancelled) is refused with 409
unless the payment has no bill allocation, payout, outstanding hold source or consumption.
It appends `balance_action_reversal` (amount -X) and a `funding_consumption` of the payment
(`reversal_action_id` = the action) and flips the status, then runs the deferred 0045
ledger check immediately (a violation is a 409, never a commit-time 500). The payment row
is never touched and no refund exists. Repeating `cancelled` only edits descriptions. Other
PATCHes may change `reason`/`description`; any other attribute must equal the stored value
(409). Create and Cancel events carry the same body a GET returns.

Events. TMF654 is a v4 API, so its events keep the v4 notification structure (`eventId`,
`eventTime`, `eventType`, `event`) like TMF676's; `app/events/types.py` holds the one
`V4_APIS` set that the envelope and the hub share. P4c's hub was v4 but P4d's first events
went out in the v5 shape (the Swagger 2 schema tolerates the extras); fixed with a test that
pins the top-level keys.

Idempotency hash. The P4b request hash now reads an integer and a decimal as one number
(`10` = `10.00`, as its docstring promised; a bool stays distinct). Migration `0046` is on
the VPS, so a key stored before this fix whose body had an integer answers a retry with 409
instead of a replay; prepaid creation is gated and keys are short-lived retries.

Closed accounts. `/topupBalance` stores a refusal on a Closed account (`failed`), but a
direct payment to a Closed prepaid account is recorded as a completed top-up: a failed
top-up cannot link a payment (0045), and the payment already happened.

Gap ledger: automatic top-up, voucher and `relatedParty` are recorded as accepted
deviations; the top-up endpoints leave `PENDING`. MCP: five tools (`*_topup_balance`),
over HTTP without the idempotency header.

## P4e: adjustments (2026-10-23)

`adjustBalance` has all five operations (`app/api/adjust.py`; the row functions sit with the
top-up's in `app/repository/balance_actions.py`, whose `get`/`page` now take a `kind`), no
migration. The amount is signed and nonzero. A credit inserts a completed `adjustment` action
and its one `adjustment` funding source (available at the injected clock's instant). A debit
quotes `funding.plan` under the account lock (so it is limited to L - H less unpaid charges,
the same rule a purchase obeys) and writes one `funding_consumption` per planned source, in
funding order: payments, credit notes, then adjustment, transfer and reversal sources. A debit
above the spendable funds is a business refusal: stored `failed` with 201 and a Failure event,
like the top-up's; so is a Closed account (either direction).

Cancellation (completed -> cancelled only) appends `balance_action_reversal` and flips the
status. A credit's reversal consumes its own source, and the deferred 0045 bounds check, run
immediately, refuses it with 409 when any of it was spent, allocated, paid out or held. A
debit's reversal inserts one `reversal` source per original consumption (same amount,
`consumption_id` = that consumption), so each consumed source is restored exactly once and the
money is spendable again; a debit is always cancellable. Repeating `cancelled` only edits
descriptions. `reason`/`description` are the only other editable fields. Create, Cancel and
Failure events carry the GET body; both POSTs take an Idempotency-Key and DELETE of a failed
one tombstones it.

TMF654 AdjustBalance_Create skips `partyAccount`: the owner follows the bucket, and a sent
`partyAccount` is a 400 (unknown attribute). `adjustType` is returned as `oneTime`; `recurring`
is a 422. `relatedParty` is recorded as an accepted deviation. MCP: five tools
(`*_adjust_balance`); the five endpoints leave the gap ledger's pending list. The topup module's
reference check is shared (`topup.check_references`).

## P4f: operator holds (2026-10-23)

`reserveBalance` is the client-created hold; no migration. A created hold is a
`reservation` `balance_action` on a plain `funding_hold` (never command-owned), status
`created` while the hold is outstanding, `cancelled` once released, `failed` for a refusal.
0045's checks already tie the status to the hold. `completed` and a command-owned hold
belong to the owned reservations a later slice may project; PATCH/DELETE answer 409 for them.

The end is the request's `validFor.endDateTime`, read back with `balance_actions.expiry`
(creation refuses an unparseable or past end; a zone-less one is UTC). Half-open: at the end
instant the hold has lapsed. `release_expired(conn, account, now)` runs under the account's
row lock at every place that spends: `settle_account` (so payments, refunds, bill payment
and the billing run), the prepaid order's funding check, the worker's interval posting,
adjust debits, a new hold, and PATCH. `release_all_expired` is the worker's sweep at the top
of `process_commands`, so a lapse is released within a wake-up even with no spend, and the
released hold re-opens a command's shortfall scan. Reads do not release (no lock, no clock
write): between the end and the next spend a GET can show `created` while the money is
already spendable.

A hold is limited to the spendable funds (the same plan a debit uses), positive, and in the
bucket's currency. `amount` is required although the schema leaves it optional (accepted
deviation, with `relatedParty`). Suspended accounts may hold; a Closed one is a stored
refusal. Tests: `tests/test_api_reserve.py`.

## P4a: balance action and source schema (2026-10-23)

Migration `0045` adds `balance_action`: kind (topup/reservation/adjustment/transfer),
TMF654 status, the original checked request, Decimal amount (and transfer cost/owner),
bucket and receiver bucket (composite keys enforce account, currency and prepaid mode),
reason/description (editable), a separate `failure_reason` set exactly when failed,
operator, requested/confirmed/cancelled instants, and unique links to its done payment
(top-up), hold (reservation, accepted transfer) and command (owned reservation or
transfer; the hold must be that command's). Per-kind CHECKs encode the decided rules:
top-ups/adjustments are never `created`, transfers never `cancelled`, a receiver-paid
fee is at most the principal, an accepted transfer needs another same-currency bucket
(a refusal such as `sameBucket` is still storable). Money, references and final
statuses are immutable; only failed actions may be deleted (tombstones are P4b's).

`balance_action_reversal` is the compensating ledger entry, at most one per action
(primary key). `funding_source` adds the adjustment, received-transfer and reversal
source kinds; `funding_consumption` records exact source use by debits, outgoing
principal and credit reversals. Allocations, payouts and hold sources gain a
`funding_source_id` beside payment/credit note. One deferred check bounds every source
by allocations + payouts + outstanding holds + consumption, and ties each action to its
entries: a debit or completed transfer consumes exactly its amount, a credit or
received transfer introduces exactly one source, a cancelled top-up/positive credit's
reversal consumes that original source, a cancelled debit restores each consumed
source once as a `reversal` source, holds follow the action status, and an operator
hold is never command-owned. Downgrade refuses any retained action or source.

`account_balance` (L, TMF666 `currentBalance`) adds signed adjustments, reversals and
completed transfers in/out; settlement and funding snapshots count consumption, and
holds/conversions carry the new source column. Repositories detect the 0045 schema so
older-schema proofs keep working. `Snapshot.reserved` is H; spendable is L - H.
A hypothesis test drives top-ups, credits, debits, payouts, holds/releases, transfers
and reversals on two accounts with unbilled charges and checks L, H and L - H against
an independent oracle (and that funding refusals happen exactly when the oracle says).
Open for later slices: the TMF654 projection of `failure_reason`, operator-hold expiry
(P4f), transfer fee posting (P4h) and the internal bill view's source label for the
new kinds. No API or worker change.

## P3i: internal command operations (2026-10-23)

The internal contract gains `GET /internal/v1/prepaid/command` (newest acceptance
first, `offset`/`limit`, equality filters `billingAccount.id` and `state`),
`GET /prepaid/command/{id}` and `POST /prepaid/command/{id}/retry`, with MCP tools
`list_prepaid_commands`, `get_prepaid_command` (read scope) and
`retry_prepaid_command` (write scope). `PrepaidCommand` shows kind, internal state
(not a TMF enum), due date, acceptance/attempt/next-retry times, the cumulative attempt
count, the completed result's `outcome`/`reason`, the technical `error`, and
`needs_topup` (same shape as on account products) only while pending. Links are
derived, not stored: the order of an order command, products from the request
(quotes, future products, terminations, a renewal's predecessor grant) and the
command's grants, its billing run and that run's bills and credit notes. Reads take
no identity or ownership lock.

Retry takes no body and **never posts**: it calls the journal's `retry` (identity
lock, state recheck), keeping identity, request, hold and attempt count, clearing
the error and backoff; the dispatcher attempts it on its next tick even inside the
automatic backoff. 202 with the command; 409 unless failedTechnical, also when the
injected clock precedes its recorded history. No worker request row is used, since
command dispatch already scans pending commands. No balance-action link exists yet
(P4a); the internal pending-offering product field stays with P5. No migration.

## P3h: offering change at renewal (2026-10-23)

A prepaid modify item with `productOffering` takes effect only at the next renewal:
the latest grant's paid-through date, or today once that date has passed (a
funding-suspended product). Any other date is a stored rejection
`offeringChangeNotAtRenewal` (201, like every order rejection, rather than a bare
409); a product without a first grant is `productNotStarted`, one with a set
termination `productAlreadyEnded`. The order completes at once and retains the
change in `product_offering_change`, with `effect_announced_at` NULL until applied.

The renewal prices the product's offering on its own date, so the retained change
is what it buys; a shortfall records the new plan's amount, leaves the product
funding-suspended and the change pending, with no fallback to the old plan. A
later funded renewal, including a resumption after a gap or a P3f resume, applies it
in the posting transaction (`effect_announced_at`, TMF637 AttributeValueChange). The
daily announcer skips prepaid changes. Before its date the change is cancelled like a
postpaid one; afterwards it is refused. TMF637 shows the retained offering from its
date even while unfunded (the internal pending-offering field belongs to P3i/P5).

Migration `0044` relaxes the modify-item CHECK: one item may carry the offering and
`realizingResource` together (a status change stays alone); its downgrade refuses
while such items exist. Prepaid applies both at the boundary in one transaction; the
number moves on that date whether or not the renewal is funded and is never
repriced. Postpaid combined items are rejected `combinedChangeUnavailable`.
Number-only prepaid changes stay `prepaidLifecycleUnavailable`. P3i is next.

## P3g: termination credits (2026-10-23)

A prepaid order of delete items only ends its products on the requested business
date (today or later; the date itself is excluded) and releases their numbers then.
Acceptance writes no money: the products are `terminated`, or `pendingTerminate`
until a future date, and the order stays `inProgress` with one `order` command due
on that date. A product without a first grant is `productNotStarted` (cancel its
purchase instead); deletes mixed with other actions stay
`prepaidLifecycleUnavailable`. A Suspended account may terminate (cleanup). Once
accepted, a prepaid termination cannot be cancelled (409).

The billing owner posts the command: each grant still covering the date credits its
unused dates, by difference as postpaid refunds do: the net for the used dates is
rounded once and the credit is that minus the charge, tax lines likewise at the
charge's rates. A same-day termination credits the whole interval, an expired one
nothing; a charge is credited at most once. Each credit is a `refund` ABR of the
command run with a settled `offCycle` credit note dated on posting, which is the
spendable source; nothing is spendable before. Products, items and order complete
with TMF637/622 events in the same transaction. A renewal of an ended product is
retired (completed `cancelled`, `productEnded`, any owned hold released once)
instead of staying pending, so the account can close; this replaces P3e's
"pending and unattempted" for ended products. The observer keeps
`pendingTerminate` inside a live grant. No migration. P3h is next.

## P3f: manual suspension, resumption and account state (2026-10-23)

Prepaid modify items with `product.status` complete synchronously on the order's
day; future-dated status changes and mixed purchase/status orders stay
`prepaidLifecycleUnavailable`. A suspend order sets the manual cause and keeps the
paid interval and its charge; at expiry the producer skips the product. A resume
order lifts only the manual cause: inside a live grant the product is active again;
after expiry it stays funding-suspended and the order queues the expired grant's
renewal (the producer's identity, locked before the account), which posting
authorizes from today. A funding suspension is not a manual one, so resuming it is
`statusUnchanged`. A product without a first grant is `productNotStarted` (cancel
its purchase instead). The stored status follows effective entitlement and a
StateChange is emitted only when it changes.

One status change per product and day stays the rule, so a same-day undo is
cancelProductOrder, as for postpaid: it restores the latest applied change's manual
cause and the effective status; a renewal queued by a cancelled resume stays
pending, blocked by the restored cause. On a Suspended prepaid account a resume is
`accountNotActive`; suspensions and cleanup (cancellation, P3g terminations) remain
allowed. Closing a prepaid account is a 409 while it holds money, has unfinished
commands or products not yet ended on today's business date; the remaining credit
stays on the closed account. Postpaid suspension, cancellation and closing are
unchanged. No migration. P3g is next.

## P3e: renewal and resumption producer (2026-10-23)

Each worker tick, after operator requests, produces renewals before dispatching
commands and observing entitlement. A renewal is due when a product's latest grant
ends on or before today, its prepaid account is Active, it is neither manually
suspended nor ended, and no renewal exists for that grant. The command id is a
UUIDv5 of the predecessor grant, so production is idempotent without a migration.
Per account, identities are locked before the account, candidates are revalidated
under the lock and queued in due-date, then product order. A late producer keeps the
paid-through due date; posting then buys one full interval from today (P2c3b4).

A shortfall leaves the renewal pending with its reason; entitlement already ends at
paid-through and the worker stores the funding suspension. A pending command with a
recorded shortfall is attempted again only after a later payment or released hold
on its account, or on a later business date (the fallback rescan that also picks up
price changes), not on every tick. Since #340 a funding source is a credit signal too and
the date-only rescan is decided in bulk (see the #340 section). P4d top-ups must join that credit signal. Manual
suspension, a Suspended/Closed account or an ended product leaves a produced renewal
pending and unattempted rather than a technical failure. A funded resumption makes
the product active again in the same tick. No schema, API or public prepaid creation
change. Tests cover the checkpoint flow end to end (purchase, renewal of one product
while the other runs out, same-day quiet, next-day rescan, payment wake-up and gap-free
resumption with TMF637 events), idempotent late production and each blocking cause.
Checkpoint 1 holds; P3f is next.

## P3d: future starts and pre-grant cancellation (2026-10-21)

Future add orders retain pendingActive products, number claims and an immutable
product-set intent without reserving funds. The due worker resolves price and tax
at the actual authorization date and reserves/posts the entire order atomically.
A funding shortfall retains its reason, with no hold or partial grant; a later tick
selects its own date and quote. Initial order price fields remain acceptance estimates;
the funded interval and bill retain the actual authorization quote. Immediate starts
continue to retain their accepted quote and money.

Cancellation takes the command identity before any order/account/product locks,
serializing it against posting. Before the first grant it cancels the complete tree,
voids number claims and releases any command hold atomically. The journal records a
completed business result with outcome `cancelled`; dispatch and posting replay do
not purchase service. After a grant cancellation returns 409. No schema migration
or public prepaid account creation is included. Tests cover no-funds scheduling,
claims, due-date dispatch, actual-date price/tax selection, shortfall recovery,
immediate/future cancellation, cancellation rollback and refusal after funding. P3e is next.

## P3c: immediate prepaid purchase lifecycle (2026-10-21)

Immediate add-only prepaid orders quote full periods and account tax exemptions
under the account/catalog locks. Acceptance checks the combined tax-inclusive
spendable amount before writing products or number claims. Shortfalls store a
rejected order with `insufficientFunds` and no hold, command, product or claim.
The command shares its order UUID. Accepted tax exemptions remain in its immutable
quote and the order price projection. Successful acceptance retains one exact-source
command hold, the complete quote set, pendingActive products and number claims,
with every item and order inProgress.
Draft submission follows the same path; drafts reserve nothing.

The billing owner validates the command against its complete pending order before
posting. Exact-source conversion, all grants/documents, active products, completed
items/order and TMF622/637 StateChange events commit in the existing posting
savepoint. Failure leaves the accepted reservation and pending tree intact; replay
returns the retained result without duplicate lifecycle events. Delayed dispatch
keeps accepted money and buys actual-date coverage. Funding-suspended renewals can
post when funded; the independent manual cause still blocks them.

Future starts and other prepaid lifecycle actions are rejected until their owning
slices land. Public prepaid account creation remains a 422. Tests cover flat/nested
multi-item starts, exact funds and tax shortfall, zero-price purchases, acceptance
and posting rollback, replay, draft submission, delayed dispatch and concurrent
purchases. Next is P3d: future starts and pre-grant cancellation.

## P3b: entitlement and independent suspension causes (2026-10-21)

Migration `0043` retains manual suspension separately from prepaid funding expiry.
One effective-state function uses the injected business date: terminal lifecycle,
manual/account suspension, live grant, pending first grant, then funding suspension.
The internal account product view adds authorization, paid-through, suspension cause
and retained needs-top-up amounts. TMF637 reads use the same effective state.
GET does not write or emit; the worker observes states under account/product locks,
stores transitions and emits TMF637 StateChange events at observation time.
Daily activation skips prepaid products. Public prepaid creation remains gated.
Tests cover precedence, exact half-open expiry, read purity and worker persistence.
P3c is next.

## P3a: daily-run isolation (2026-10-21)

The locked billing-account projection carries its rating mode into the pure daily
core. Prepaid computation returns no ABRs or bills, including refunds; command
posting owns those finances. Daily membership, outcome counts, issue/settlement,
postpaid visibility cutoffs and replay remain unchanged. Prepaid on-demand requests
collect only existing unbilled ABRs through the successful daily run's date (including
command-run sources), or finish rejected without an empty bill. They never authorize
a new interval. Public prepaid creation remains unavailable.

Tests exercise a live prepaid product across daily and cycle boundaries, replay,
and on-demand requests with and without an existing charge. P3b is next.

## P2c4b: durable funding shortfalls (2026-10-21)

Migration `0042` adds append-only funding observations keyed by command and injected
decision instant. After posting rolls back a shortfall, dispatch retains the exact
required and spendable amounts under the existing command/account locks. Funding
decisions leave the command pending with no technical backoff or attempt increment.
A technical retry that reaches funding checks returns to pending. Repeated identical
decisions at the same instant are idempotent; conflicting decisions are refused.
Later decisions cannot precede retained funding history.

The internal `needs_topup` projection returns the latest observation only while its
command is pending. Successful posting or technical failure supersedes that current
projection; earlier observations remain for audit. A lost transaction retains neither
the observation nor partial posting. Downgrade refuses retained funding observations.
No public API, entitlement projection, command producer or account creation is enabled.
Next is lifecycle integration, including separate manual/funding suspension causes,
paid-through entitlement and atomic order completion.

## P2c4a: retained-command dispatch (2026-10-21)

The billing owner dispatches existing order/renewal commands on each tick, after
operator requests and before event delivery, independently of daily billing gaps.
No producer or public prepaid creation is enabled. Transfers remain untouched.
Candidates exclude completed, future-dated and backoff-delayed commands. Accounts
are visited by UUID; each account pass locks command identities before its account
and orders attempts by due date, product UUID, then command UUID. Batch orders
remain atomic and use command UUID for their position within a tied date.

The clock is read again after locking the account. Posting runs in a savepoint;
technical failures roll back posting effects before the existing journal records
diagnostics and its one-minute/five-minute/hourly backoff. A later tick retries
with the original identity. Each candidate is attempted at most once per tick;
a failed command does not block later candidates. Account-pass commit retains
successful effects and diagnostics together. Loss of that transaction leaves them
retryable. Checkpoints run between accounts; heartbeats follow each account pass.

Funding shortfalls remain pending and are logged, with no technical backoff or
financial effects. Durable funding-reason projection is deferred to lifecycle
integration: the journal currently permits only terminal results and technical
errors. This foundation does not generate renewals, change product/order states
or expose entitlement. Suspended accounts/products still fail the posting guard.
These remain prerequisites before prepaid creation can be exposed. No schema or
API change is included.

## P2c3b4: actual-date renewal recovery (2026-10-21)

The internal poster accepts a renewal intent containing `previous_interval_id`.
The retained interval must belong to the command account, be the product's latest
grant, and end on the command due date. Under command/account/product locks the
billing owner selects the recurring price and account tax exemptions at midnight
of the actual authorization business date. It reserves the tax-inclusive total
and posts the settled document, grant, conversion and completed result atomically.
The immutable command keeps its intent; the grant keeps the resolved price/tax quote.

An on-time renewal preserves the previous grant's actual anchor and increments its
index when cadence is unchanged. A late renewal buys one full interval from today
and resets anchor/index, regardless of how many periods were missed. A changed
cadence also starts a new sequence. No waiting-gap charges or coverage are written.
Missing/ambiguous prices remain technical errors. Insufficient funds raises the
existing funding shortfall without retaining a hold or posting effects; the future
dispatcher must persist its funding reason and retry eligible work on normal ticks.
Technical rollback also discards the newly selected quote/hold, so a later attempt
uses its own actual date. Completed replay returns the original result.

Legacy held quote requests retain their date guard. This slice adds no producer,
dispatcher, entitlement projection, lifecycle mutation or public API; prepaid
creation remains unavailable. Suspended products/accounts remain ineligible until
the later lifecycle integration distinguishes manual and funding causes. Offering
change orders remain outside this primitive. Next: worker dispatch and lifecycle
integration. Tests cover delayed/current-price selection, tax validity, changed
cadence, month-end restoration, shortfall recovery, obsolete predecessors, replay
and rollback.

## P2c3b3: atomic multi-product purchases (2026-10-21)

Migration `0041` extends retained conversion with an immutable bill set and allows
one held source to split across several allocations. Existing single-bill results
keep their original shape. Multi-bill conversions require every target to be
settled, their combined total to equal the hold, and each pinned source to be
consumed exactly across the target allocations, at the release/conversion instant.
Unmapped allocations, substituted sources, mixed legacy/batch targets and partial
conversion are refused by deferred database checks. Downgrade preserves legacy
history and refuses retained multi-bill results.

The billing-owner primitive accepts an initial-order request containing an
`intervals` quote list, with one quote per distinct product. It validates the whole
set and locks products in UUID order, then creates one recurring ABR, offCycle bill,
retained document and grant per product. A single exact-total hold funds all bills
in one savepoint with the command/run result and events. Source splitting visits
target bills and pinned source rows in UUID order; it never reselects funds.
Completed replay returns retained results without posting again; conversion replay
compares the entire bill set. Initial orders delayed across business dates retain
accepted money and cadence while every product starts its full interval on the
actual authorization date.

This extends only purchase posting, not the order item-tree lifecycle. No producer,
dispatcher, number/order mutation or entitlement projection is added; prepaid
creation remains unavailable. Automatic renewals remain single-product. Next:
actual-date renewal price/tax selection and recovery, then worker dispatch and the
remaining lifecycle integration. Tests cover split mixed sources, zero totals,
concurrent replay, invalid target/quote sets, database invariant refusals, migration
preservation and rollback after a second bill, conversion or command completion.

## P2c3b2: delayed accepted starts (2026-10-21)

The internal single-product poster now recovers a held initial order on a later
business date. It retains the accepted amount, tax, price and cadence and buys one
full interval from the actual authorization date, resetting the grant anchor to
that date at index zero. The immutable command request and grant quote retain the
original quoted date; the grant's period/anchor and bill document record actual
coverage. No waiting-gap service or charge is created. Initial quotes must start
on the command due date at index zero; future commands remain ineligible.

Completed retries still return the original result. Late renewal quotes remain
refused with their holds intact: renewal recovery needs actual-date price/tax
selection, rather than reuse of an accepted initial-order quote. This slice does
not enable a producer, dispatcher or prepaid creation. Multi-product conversion
and renewal recovery remain next. Tests cover long delays, business-date timezone
boundaries, preserved funds/quote, replay and rollback at each posting stage.

## P2c3b1: atomic single-product posting (2026-10-21)

`app/billing/prepaid.py` adds an internal billing-owner-only posting primitive.
The journal request holds a strict single-product interval quote (product, offering,
price provenance, currency, net/tax lines, original calendar anchor/index and cadence).
The future producer must validate catalog price selection and exemption eligibility
before accepting this quote; the primitive validates its shape, exact cents, tax
arithmetic and authorization-date coverage. It does not reselect catalog prices.
Month/year boundaries clamp independently from the original anchor, including
monthly length one. Day/week coverage uses business dates.

Posting takes command identity then account/product locks, requires an Active prepaid
account, eligible product and exact outstanding command hold, and writes one original
recurring ABR, one offCycle bill with coverage matching the grant, its retained JSON
document and issue history, exact-source settlement, funded interval, completed run
and completed command result together. The bill is due on its authorization date.
Issuing touches only this bill; unrelated waiting bills are left to their own paths.
Completed replay returns the retained result without any new financial writes.
Exceptions roll back the entire posting savepoint and preserve the accepted hold.
The caller owns the outer commit and technical-error bookkeeping after rollback.

Command bills omit optional TMF678 `billCycle`, since the public cycle endpoints
expose daily runs only. Their financial run identity remains stored. Daily documents
retain their existing cycle reference. No public operation or schema is added.

This is the single-product part of P2c3b. Existing one-command/one-hold/one-bill
conversion cannot yet fund multiple product bills: that extension remains next,
before dispatcher/recovery. Stale-date quotes fail closed; recovery must resolve
actual-date pricing/coverage before enabling this caller. Offering changes, funding
versus manual suspension, entitlement projection and order lifecycle remain later
work. No producer, dispatcher or runtime API calls this primitive, and prepaid
creation remains unavailable.

Validation: all 1,650 backend tests pass, including 21 new cases for tax-inclusive posting/document projection,
zero grants, replay/daily isolation, rollback after snapshot/conversion/completion,
quote/account/product/hold guards, outer rollback, adjacent renewal and original-anchor
calendar boundaries. Lint, format and generated-documentation drift checks pass.

## P2c3a: retained funded intervals (2026-10-21)

Migration `0040` adds immutable `funded_interval` history: actual authorization
instant, finite nonempty half-open coverage, original anchor/index, exact amount
and tax total, an immutable quote document, and product/command/run/ABR links.
Unique command/product and product/anchor/index identities prevent duplicate grants;
GiST exclusion prevents overlapping coverage. Zero-price grants are valid.

Composite foreign keys require a prepaid account and matching product ownership,
an order/renewal command for that account, its own command run, and an original
recurring ABR with identical product/account/run/currency/coverage/amount/tax.
Referenced financial identity cannot subsequently be changed underneath a grant.
The retained command and product already protect account history; no independent
account-history registration is needed. Downgrade refuses retained grants, while
an empty upgrade/downgrade preserves existing financial history.

This is persistence only. The future posting caller must validate the quote shape,
calendar anchor/index and authorization business date, then create the charge,
settled offCycle bill, hold conversion, grant and completed command result in one
transaction under billing ownership. The table does not itself prove settlement
or expose entitlement. No posting, producer, dispatcher, projection or API is enabled;
prepaid creation remains unavailable. Next: P2c3b atomic posting and document
projection, then worker dispatch/recovery and internal retry operations. Later
slices still own prepaid order/renewal lifecycle, action APIs, MCP/UI and release proofs.

Validation: all 22 new persistence cases pass. The first full run passed 1,628
tests and failed only the non-superuser migration test's old table count (51
instead of 52); its upgrade/downgrade steps succeeded. Both schema-count assertions
now include the new table. Generated database references were refreshed.

## P2c2: command billing-run identity isolation (2026-10-21)

Migration `0039` preserves daily run IDs/history and replaces date uniqueness with
a partial daily-date index. Internal `kind` is `daily` or `prepaidCommand`; each
command run has one unique retained journal reference. Kind, command, date and run
UUID are immutable; command runs cannot be deleted or downgraded away. Daily
outcomes, blocking references and worker retry requests reject command runs in SQL.
Existing inserts default to daily. Command runs reuse the existing `scheduled`
trigger as internal bookkeeping; kind, rather than trigger, defines isolation.

`prepaid_commands.ensure_run` shares the command identity lock, returns the retained
run on replay (including a later date), and rolls back with its caller's effects.
The future dispatcher must take billing ownership first. No runtime caller,
command-run completion/recovery or financial posting is introduced here.

Daily scheduling, takeover, failure/backoff, health, internal list/count/detail/retry,
TMF bill-cycle list/retrieve and on-demand readiness explicitly select daily runs.
The legacy daily billing adapter refuses command runs. Financial history joins
remain inclusive so future command ABRs and bills retain their real run references;
the prepaid document projection belongs with posting. Tests cover same-date
coexistence, replay, rollback/concurrency, all command states, API/count isolation,
database reference guards and populated migration round-trip/retention.

Prepaid creation remains unavailable. Next: posting/funded intervals, then owner
dispatch/recovery and internal retry operations.

## P2c1: durable command journal foundation (2026-10-21)

Migration `0038` adds `prepaid_command`: immutable account/kind/due-date/request
and acceptance instant, retained completed result, and technical retry state.
Internal kinds are order, renewal and transfer; producers will validate their
kind-specific request documents when they land. This foundation has no producer,
dispatcher, public/internal API or billing-run change. Prepaid creation stays gated.

Repository operations share the funding-command advisory identity and acquire it
before account/child locks. Enqueue replays the stored command, including after
completion; changed account, kind, due date or request is refused. A rolled-back
enqueue consumes no identity. Existing command holds must share the journal account,
enforced in either insertion order; legacy foundation holds remain valid without a
journal. Command history independently prevents mode changes, owner changes and
deletion even without a bucket, payment or hold. Downgrade refuses any retained
command, including a completed business refusal.

Execution eligibility checks the due business date and technical backoff. There is
no persisted running state: the identity lock lives through the transaction; death
before commit leaves the prior command and funding state. Completion writes its
result in the caller's financial/event transaction. Technical failure must be
recorded in a fresh transaction after rollback, retains holds and schedules retries
after one minute, five minutes, then hourly. Manual retry only accepts
failedTechnical and retains the original identity/request and attempt count.
Business refusal is a completed result, not a technical failure. The journal keeps
the last technical error and cumulative attempt count, not a per-attempt log.

Fourteen tests cover replay/conflicts, rollback, exact-source conversion and result
atomicity, retry backoff/manual retry, due dates, concurrent identity locking, hold
ownership, retained request/result and account history, populated upgrade/downgrade
and downgrade refusal. The existing non-superuser migration proof covers `0038`.
Next: command billing-run identity isolation, posting/funded intervals, then owner
dispatch/recovery and internal retry operations. This slice does not implement
those runtime recovery paths.

## P2b6: consuming paths respect held sources (2026-10-21)

Settlement snapshots carry outstanding holds separately from consumed amounts.
Automatic settlement and explicit payment items use only the unheld remainder of
each payment or issued credit note; bill outstanding totals remain unchanged.
Funding reconciliation continues to count allocations/payouts as consumption and
holds separately, so neither is subtracted twice.

Refunds settle around holds first, then cap payouts by both eligible unheld sources
and the reconciled spendable ledger balance. Payment-specific refunds retain their
source restriction. This protects funds reserved on other sources even when unpaid
obligations remain. Release makes the exact sources eligible again. Existing account
locks and combined database bounds continue to serialize all consuming paths.

No schema or public contract changes, worker command, or runtime hold creator is
introduced; prepaid creation remains unavailable. Tests cover automatic settlement
around mixed payment/credit-note holds, explicit refusal without allocations, and
unrestricted/payment-specific refund limits and release. Next: durable worker
commands, posting and recovery.

## P2b5: durable hold conversion (2026-10-21)

Migration `0037` retains one conversion result per command-owned hold and target
bill, plus one immutable allocation mapping per held source. Composite ownership
keys and deferred checks require a released hold, matching conversion/release
instants, a settled bill for exactly the held total, and allocations matching every
pinned source and amount. Zero holds convert to zero bills without allocations.
The checks also cover subsequent bill/allocation writes. Conversion history cannot
be updated or deleted; downgrade refuses retained results. Existing holds survive
an empty conversion migration round-trip.

`convert_for_command` acquires the same command identity lock before account/child
locks. It consumes only that command's outstanding hold, without re-quoting funds.
The caller creates the charge and issued bill in its transaction; conversion writes
allocations, bill state/history/events and the result together in a savepoint, then
checks bounds and funding reconciliation. Failures restore the outstanding hold.
Committed retries return the original hold without repeating financial or state
writes; a changed target or account fails. A released hold cannot be converted.
Conversion requires an unallocated issued bill; partial/multiple-bill conversions
are outside this foundation. Allocations use the existing automatic kind and run
state-change cause, since the future billing owner executes these commands.

This introduces no API, worker caller or funded interval. Prepaid creation stays
unavailable. Next: consuming-path integration around held sources, then the durable
worker command/result journal, posting and recovery. Tests cover exact mixed-source
conversion, zero totals, committed and concurrent retries, rollback, technical
failure recovery, invalid requests, immutable history and migration retention.

## P2b4: command-owned hold retries (2026-10-21)

Migration `0036` adds an immutable one-to-one internal command UUID/hold mapping.
Composite foreign keys retain the owning account; existing anonymous holds remain
unchanged. Ownership cannot be reassigned or deleted, even after release, and
downgrade refuses retained command identities. The existing account-mode history
guard covers these commands through their mandatory hold, including zero holds.

`reserve_for_command` takes a transaction-scoped identity advisory lock before
account/source locks. A committed retry with the same account and amount returns
the original hold, including after release, without quoting or reserving again.
A changed account/amount is refused; rollback retains neither identity nor hold.
The first reservation instant is retained; a retry's instant is not request data.
Release requires the matching command owner for owned holds. Anonymous foundation
holds retain their existing release behavior. Callers own the transaction and must
acquire this identity before any account or child locks.

This is only internal reservation ownership, not the HTTP response idempotency
journal or worker command/result/recovery journal. No runtime caller or API is
enabled, and prepaid creation remains unavailable. Next: durable consumption and
conversion to allocations, consuming-path integration, then worker recovery.
Tests cover committed/released replay, conflicting requests, rollback, concurrent
duplicates, immutable/cross-account ownership and migration preservation/retention.

## P2b3: retained funding holds (2026-10-21)

Migration `0035` adds `funding_hold` and immutable `funding_hold_source` amounts
referencing existing payments or settled negative bills. Composite foreign keys
preserve the owning account/currency. Holds retain all history after an irreversible
release; zero holds require no sources. Deferred checks require exact source totals
and bound allocations + payouts + outstanding holds by each source amount. Account
locks serialize inserts, releases and source updates, including direct SQL writes.
Source updates also recheck bounds; retained hold history prevents mode changes,
owner reassignment, deletion and lossy downgrade.

The funding repository loads outstanding source holds, subtracts them once from
spendable ledger balance and from source availability, and offers internal reserve
and idempotent release methods. Reserve re-quotes under the account lock, requires
exact cents and checks its constraints before returning. Neither method commits.
Reservation creation has no retry identity yet: no API, order or worker calls it.
Expiry, ownership by commands, conversion to allocations and new debit/credit source
kinds belong to subsequent slices. Legacy settlement/refunds currently fail closed
at the shared bounds if a hold conflicts; integration must make them select around
held funds before any public caller creates holds. Prepaid creation stays unavailable.

Tests cover mixed sources, debt/obligation caps with holds, irreversible release,
zero holds, exact amounts, cross-account references, combined allocation/payout/hold
bounds, direct-SQL serialization, migration round-trip and downgrade retention.
Next: durable consumption/command ownership and consuming-path integration, then
worker recovery. The full prepaid milestone remains gated.

## P2b2: obligation-aware funding plans (2026-10-21)

The legacy repository now quotes exact source amounts under the account lock.
`plan` first reconciles the snapshot, caps a purchase at positive spendable ledger
balance, then selects unused payments and issued credit notes. Source credit alone
is insufficient: held bills and unbilled charges already claim part of that money.
Debt yields zero available funds; zero quotes are valid. Invalid amounts are refused
before loading, and inconsistent history remains a technical failure even if the
requested purchase would also exceed available funds.

A plan is read-only and valid only within its transaction. It is not a reservation;
repeated quotes do not accumulate holds. A future consuming caller must persist its
hold or consumption before releasing the lock. Tests cover obligation caps, exact
mixed-source selection after settlement, debt, invalid amounts, repeated quotes and
technical failures. No runtime caller, schema or API change is included. Next:
persisted holds/consumption and combined database bounds, followed by consuming-path
integration and worker recovery. Prepaid creation stays unavailable.

## P2b1: legacy funding repository (2026-10-21)

`app/repository/funding.py` loads a reconciled legacy snapshot under the account
row lock. It reuses settlement's payment and issued credit-note consumption totals,
including both allocations and immutable payouts. Obligations include positive
bills in every state (including onHold), less allocations, and net unbilled ABRs
including tax. Independent ledger reconciliation refuses mismatched history as a
technical failure. Negative ledger balances remain valid debt, not source corruption.

Credit-note ordering uses bill-date midnight UTC as a deterministic date key,
not a claim about its actual issue instant. Unissued negative bills are not funding
sources; a snapshot before their issue fails reconciliation rather than granting
premature credit. Future callers must finish issuing credits before loading funding.

Database tests cover empty/foreign-account funds, settlement invariance, unbilled
charges, held/unissued bills, combined payout/allocation consumption, unissued
credits and inconsistent legacy history. This adapter has no runtime callers yet.
Next: persisted holds/consumption and combined database bounds, then wire every
consuming path. No migration, API or worker behavior changes in P2b1; prepaid
creation stays unavailable.

## P2a: pure shared-funding core (2026-10-21)

`app/billing/funding.py` plans exact source amounts after combined consumption and
outstanding holds. It preserves settlement's payment-before-credit-note ordering,
then orders adjustment, transfer and reversal credits by kind, availability instant
and UUID. The repository must supply one account/currency under the account lock;
this pure layer neither writes reservations nor enables a financial path.

Source corruption (including overconsumption, duplicate identities and nonfinite
amounts) is a technical failure, distinct from an ordinary insufficient-funds result.
Reconciliation requires unused sources minus unpaid obligations to equal the ledger;
unpaid obligations include unbilled charges and issued bills' remaining amounts.
Allocations reduce both sides of that source/obligation difference, leaving the
ledger unchanged. Spendable ledger balance then subtracts outstanding holds once.

Tests cover combined holds/consumption, exact fractional selection, deterministic
ordering, duplicate and invalid sources, zero funding, shortfalls and reconciliation
before/after settlement. No migration, API, existing settlement or worker changes
are included. P2b remains shared persistence, legacy-source reconciliation and
database combined bounds, followed by wiring every consuming path and durable
worker/recovery. Prepaid account creation remains unavailable until those proofs pass.

## P1: account mode and balance identities (2026-10-21)

The first prepaid persistence slice is migration `0034`: checked
`billing_account.rating_type`, defaulting existing and new accounts to `postpaid`,
and `prepaid_bucket`. A prepaid account cannot carry a positive credit limit.
The bucket has stable UUIDs for both TMF654 bucket and accumulatedBalance, one
account/currency pair, a composite foreign key requiring a matching prepaid owner,
and an injected creation instant. It stores no balance. Bucket identities cannot
be updated or deleted; account deletion cannot discard them. They will be created
with financial history by the future funding path, not by reading an empty account.

Mode changes are refused after existing order, product, payment, refund, bill,
allocation or run history, or a retained bucket. Repeating the same mode is harmless.
The account history registry includes buckets for owner-change/deletion checks.
Downgrade to `0033` refuses any prepaid account or retained bucket; an entirely
postpaid database can round-trip without changing existing finances or refunds.
Future action/command tables must extend the history guard when they land.

Prepaid POST/PATCH remains a 422; no TMF654 route, financial operation or worker
behavior is enabled. P1 is an additive foundation within stages 3–4, which stay
gated together until shared source accounting and recovery proofs pass. Next is
shared funding sources, reservations and combined consumption bounds, followed by
the durable worker path. Tests cover constraints, identity retention, mode history,
populated financial migration and downgrade barriers; the existing database-owner
test covers upgrade/downgrade without superuser privileges.

Validation: implementation `5992cde` passed all 1,507 backend tests, lint/format and
generated-documentation drift checks in the commit hook. All 12 new persistence
tests pass. The prior full run passed 1,506 tests with one old schema-count assertion
still expecting 44 tables; correcting it to 45 resolved that failure.

The human requested completion of the prepaid design, choosing remaining policies
according to TMF, then committing/pushing design changes. Development will occur in
another session. The authoritative hand-off is:

- [The prepaid design as built: scope, contract pins and policies](../prepaid-design.md).

Subscriber numbers ship independently before prepaid. The design selects TMF639
v5.0.0 and stable TMF654 v4.0.0, including explicit contract deviations. These specs
were pinned in the design and are now vendored by N1 below. This record does not
change the current runtime: accounts remain postpaid until the complete funding
core is ready.

Key decisions for development:

- MSISDN is a LogicalResource, exclusively assigned with usageState busy. Assignments
  are effective-dated, constrained against overlap, changed/released through orders.
  Quarantine defaults to zero days; future accepted claims are not revoked by a
  setting increase. No new network provisioning integration is implied.
- Prepaid is an account mode with full service intervals and no overdraft. Balance
  and reservations share existing source-accounting protections; no independent wallet.
- Only the billing owner posts charges, settled offCycle documents and funded
  intervals. Transfers queue commands, are final, and use new opposite transfers for
  corrections, because the selected v4 PATCH has no cancellation status.
- Service expires at persisted paid-through even if the worker is late. Recovery
  never purchases historical missing intervals: it starts one new interval today.
  Manual suspension and account state cannot be lifted by a top-up.
- Optional durable Idempotency-Key supports client retries; immutable commands and
  constraints independently protect financial posting. All consuming paths honor holds.
- Top-up cancellation uses compensating entries and never edits an immutable receipt.
  Operator reservations require an explicit amount. Omitted payment method records
  an unspecified receipt, never an assumed cash collection.
- The v4 history schema's mandatory receiver is relaxed only for non-transfer actions,
  explicitly recorded as an upstream-schema exception; no fictitious number is returned.

Existing account, billing, worker, order, catalog, event, MCP and UI decisions remain
the runtime rules until their implementation slices land. The hand-off identifies
which rules change for prepaid while preserving postpaid behavior. No migration,
runtime code, progress-completion claim or deployment is part of the design change.

## N1: pinned contracts and executable fixtures (2026-10-20)

Development starts with byte-identical copies of both pinned specifications in
`spec/`. Provenance uses the existing upstream blob and SHA256 checks. The shared
contract validator loads both APIs. `tests/test_prepaid_contracts.py` checks number
states, action create/response bodies, required fields, monetary Quantity versus
Money, future enum values and the conditional history exception. These are contract
fixtures, not runtime or database-enum coverage; PostgreSQL enum locks will be added
with the corresponding migrations.

The base-path probe resolves server variable defaults, including TMF639's entire
URL held in apiRoot. The gap ledger and progress scope include all 36 non-listener
operations as absent/planned. These APIs remain separate from the existing attribute
probes until routes land; implementing a route requires enabling those probes and
replacing endpoint gaps with the exact attribute gaps. Policy restrictions and the
unreachable transfer-cancel event are recorded separately as comments, not confused
with measured endpoint coverage. Existing API and billing behavior is unchanged.
The event inventory test locks all 16 new listener names separately from the existing
runtime registrations, including the planned but unreachable transfer-cancel event.

Validation: three full backend passes, 1,433 tests each (dev/CI/dev), backend lint,
frontend lint and 277 frontend tests in each configured timezone pass. All ten
recorded Git blob hashes match GitHub at their pinned revisions, and all ten SHA256
checks pass. Documentation drift and decision/link checks pass. Negative proofs
discarding the server-variable default and waiving the transfer history receiver
failed the targeted tests as expected; both mutations were reverted. The restored
24 contract fixtures pass. Full backend logs: `/tmp/bss-n1-test-2.log`,
`/tmp/bss-n1-test-3.log`, `/tmp/bss-n1-test-4.log`.

Progress collection also passes all 1,433 backend tests (with its two existing pytest
configuration/import warnings) and 277 UI tests. The first render used a scope
snapshot captured before correcting the new entries from unsupported stage 6 to
the existing stage 5. Only those 36 cached scope labels were synchronized with
`scope.yaml`; the collected coverage/runtime inventory hash stayed unchanged and
the complete cached scope was checked equal to the source. Rerunning the standard
renderer passes. The generated reports show all 36 new operations unimplemented,
untested and with deployment unknown. The human authorized committing/pushing N1.
No runtime code, migration or deployment is included in N1. Next is subscriber-number
runtime implementation.

## N2: subscriber-number persistence (2026-10-20)

Migration `0032` adds `subscriber_number` and `number_assignment`; it does not
change existing products or financial rows. Numbers use unique canonical E.164
digits and the fixed `msisdn` specification. Administrative states copy the full
TMF639 enum; the inventory API will restrict writes to lock/unlock in N3.

Assignments store explicit start/release business dates and generate their half-open
`daterange`. GiST exclusions protect both number and product against overlap,
including future claims. Same-day empty ranges retain audit without claiming dates.
Cancelled versions carry a cancellation instant/item and stay outside exclusions;
restoring a conflicting version is rejected by the same constraint. Assigning,
ending and cancelling items have foreign keys, and a unique identity sequence orders
audit insertion (not transaction commits). Release and cancellation metadata must
be complete. Quarantine at acceptance is nonnegative; checks under number locks
using current configuration belong to N4, rather than a time-dependent SQL constraint.

History prevents deleting referenced numbers/products/items. Downgrade refuses any
retained number inventory or assignment history. No route, order behavior or prepaid
account mode is enabled by N2. N3 is next; deployment remains at the independent
subscriber-number milestone after N6 (no separate approval since 2026-10-21).

Validation: the full backend run passed 1,450 tests with one failure in the currency
migration regression, which upgraded to `head` and expected an unchanged global FK
list. Pinning that test to its subject revision `0031` preserves its exact comparison;
the corrected test passes. All 18 number persistence tests also pass separately.
The full run includes the database-owner NOSUPERUSER upgrade/downgrade proof.
Lint/format checks, documentation generation and whitespace checks pass. Full-run
log: `/tmp/bss-n2-test.log`. No second full run was needed for the test-only correction.

## N3: subscriber-number inventory API (2026-10-20)

TMF639 now exposes the five resource operations and POST/GET/DELETE hubs, with eight
MCP tools. Resources are canonical E.164 LogicalResource numbers with the fixed
`msisdn` specification (no fictitious catalog href). Value is immutable; descriptions,
schema-validated documents and lock/unlock are editable. Any assignment history,
including cancelled and empty intervals, prevents deletion. Writes lock the number
row; duplicate API creation is serialized by value and client-selected id.

Reads derive busy/idle and allocated/reserved/quarantined/available from half-open
business-date assignments, excluding cancelled and empty claims. Current use takes
priority over future claims, future claims over quarantine. Quarantine uses the
current nonnegative `MSISDN_QUARANTINE_DAYS` setting (default zero); changing it never
changes an existing assignment. List state filters apply before counts and paging.
Locking preserves current use and accepted future claims. Only N4 will write claims.

Creation date, computed state and specification accept equal values only. Unknown
nested attributes are refused; documents use FVO schemas on creation and Resource
schemas on PATCH, as the pinned contract declares. Unsupported resource subclasses,
network/alarm/lifecycle states and required name/value are explicit gap restrictions.
All four PATCH formats, fields selection, paging and equality filters use existing
conventions. Outbox events cover creation/deletion, administrative state changes and
descriptive changes; no-op patches emit nothing. Hubs retain existing callback checks.

The existing TMF probes now include TMF639; only TMF654 endpoints remain absent.
API/database reference docs include the new API. No migration or order/UI change is
part of N3, and deployment still requires the separate N6 release approval.

Validation uses the normal pre-commit hook (lint, full backend suite and generated
documentation drift). Its first run passed 1,468 tests and found two new fixture
errors: a nested-reference negative test omitted required `@type`, and pagination
expected insertion order despite equal fixed creation instants (UUID breaks ties).
Both fixtures were corrected without changing those runtime conventions.

## N4: subscriber numbers through orders (2026-10-20)

Migration `0033` snapshots `product.requires_msisdn` (false for existing products)
and extends the modify-item CHECK to permit exactly one resource/offering/status
change. Downgrade refuses retained requiring products or number-change orders.
The snapshot is resolved under a shared specification lock; changing its built-in
requirement after any offering order history is a 409. Other specification references
remain unchecked documents, including existing products' realizingResource values.

Add items accept a product resource list without a product id; requiring products
need exactly one inventory number. Modify items can change that number without
repricing. Deletes release it at the effective business date, including an empty
interval for an unstarted cancellation. Drafts claim only on submission. Products
are locked before all affected numbers, with number locks in stable UUID order.
Eligibility includes future claims and the current quarantine setting; the accepted
setting is retained in assignment audit. Later locks/settings cannot revoke accepted
claims. Same-day changes retain empty assignment history.

Cancellation voids assigned versions and restores released intervals only while
the existing workflow permits undoing their product effects. A conflicting accepted
reuse returns 409 before either order changes. Dependent future number changes
must be cancelled first. Current/future number projections use business dates;
TMF637 supports realizingResource.id filtering before paging and lists number-change
order items. Requiring products cannot bypass orders through a direct resource PATCH.
Transactional resource events describe accepted assignment transitions.

Tests cover API contracts, malformed nested references, missing/unknown/locked/busy/
future numbers, same-day changes, scheduled release/quarantine, cancellation conflicts,
concurrent claims, accepted-claim grandfathering, drafts, unchecked documents, outbox
events and migration preservation/downgrade. N5 UI is next; N6 owns independent release
validation and the separately approved deployment.

Deployment update: the human confirmed N4 (`47b7525`) deployed to the VPS on
2026-10-20. Before commit/push, all 1,494 backend tests, lint and documentation
drift checks passed. Local migrations reached `0033`. VPS image/smoke results were
not independently inspected in this session. N5 UI remains next, followed by N6.

## N5: subscriber-number UI (2026-10-21)

The UI now provides inventory search, number creation, assignment details and
lock/unlock at `/numbers`. Requiring offerings prompt for a number in Add product;
New subscriber optionally continues to that step after account creation. Product
number dialogs submit changes through modify orders effective today. See the
[frontend decision](frontend.md#n5-subscriber-number-ui-2026-10-21) for UX and retry
behavior. Inventory types come from the pinned TMF639 contract. Inventory, add and
modify bodies are pinned by component tests and replayed against the real backend.

Local validation passed 1,495 backend tests, 284 frontend tests in each timezone,
backend/frontend lint and five browser smoke tests. N5 is not deployed; N6 owns
the independent release proofs, progress generation and deployment approval.

## N6: subscriber-number release validation (2026-10-21)

Lock and quarantine mutations each made their targeted test fail and were reverted.
Three complete backend suites passed 1,495 tests each (CI, dev and the serial progress
collector); the initial dev attempt had one existing heartbeat timing failure under
concurrent frontend load. No timing threshold or runtime behavior was changed.
Backend/frontend lint, documentation drift, 284 frontend tests in each timezone,
five browser smokes and the collector's 284 UI tests passed. The collector retains
its two existing pytest warnings.

Progress initially found five TMF639 scope parameter names inconsistent with the
implemented paths. Correcting only those labels in the source and cached scope,
checking full equality and preserving measured evidence allowed the standard renderer
to pass. All number items are implemented/tested and nothing is unscoped. The 28
TMF654 operations remain planned. No runtime, migration or deployment change is part
of N6; shipping follows the normal cycle (commit, push, deploy, no approval).

## P4g: transfer acceptance (2026-10-23)

`transferBalance` is accepted without moving money; no migration. A POST locks the sender's
and the receiver's billing accounts in id order (so opposite transfers cannot deadlock),
releases the sender's lapsed holds, validates, and either stores a `failed` refusal or does
three writes in one transaction: a `transfer` `prepaid_command` (due today, request
`{action_id}`), a command-owned `funding_hold` of the amount plus an originator-paid fee
(`reserve_for_command`; a receiver-paid fee holds the amount alone), and the `created`
`balance_action` that names both (0045's checks tie the hold to the amount and fee). The
command is created after the account locks: its identity is a fresh UUID nobody else can
contend for. The worker's dispatcher still ignores `transfer` commands (P4h completes them),
so an accepted transfer waits `created`, holding its money, until then (done: P4h1, P4h2). The funds check
runs first (`funding.plan`) so a shortfall writes nothing.

Refusals, all stored failed (201, Failure event, `description` states why; the client's
required `reason` stays in `reason`): `sameBucket`, `currency` (the receiver bucket's
differs), a sender or receiver number or product that is not the account's, a Closed
receiver, a Closed or Suspended sender (a Suspended receiver still receives), and an
amount-plus-fee above the spendable funds. A bucket exists only on a prepaid account (a
foreign key on the mode), so a postpaid receiver is an unknown bucket, a 422, and there is
no separate `mode` refusal. A receiver-paid fee above the amount, a fee in another currency,
a non-monetary usage type and any unknown reference are 422 and store nothing.

The fee is the exact tax-inclusive Money supplied, absent meaning 0.00 with `originator`; both
are always returned. `balance_action_retained` keeps the stored request immutable, so PATCH
changes `reason` only: `channel`, `requestor` and `receiver`, which the plan would allow to
change, must be repeated as stored (409). `status`, `validFor`, `description`, `partyAccount`
and the dates, which TransferBalance_Update skips, may be echoed back equal. There is no
cancelling and no `TransferBalanceCancelEvent`. DELETE removes only a failed transfer. Five
MCP tools. Tests: `tests/test_api_transfer.py`.

## P4h1: transfer completion without a fee (2026-10-23)

P4h is split. P4h1 (no migration) has the worker complete a transfer whose fee is zero
(`app/worker/prepaid.py` `process_transfers`, run at the end of `process_commands`). Per
transfer, in one transaction: the command-identity advisory lock, then the sender's and
the receiver's accounts in id order (the API's order, so the worker, the API and opposite
transfers cannot deadlock). Under the locks the action is revalidated: both accounts
prepaid and in the same currency, the sender Active, the receiver not Closed, the hold
still outstanding. Success consumes exactly the hold's sources into `funding_consumption`
rows (the hold is released first, as in conversion), adds the receiver's one `transfer`
`funding_source` (available at the worker's clock), sets `completed` and `confirmed_at`,
completes the command and reconciles both accounts (`funding.load`) with the deferred
0045 bounds forced immediate, all inside a savepoint. A failed revalidation releases the
hold, stores the action `failed` with `failure_reason`, sends a Failure event and completes
the command with outcome `failed`. A technical error rolls the savepoint back (money,
hold and action untouched) and records `failedTechnical` with the usual backoff. No
cancellation, no completion event (none exists). A transfer with a fee is not picked up:
it stayed `created` and queued, holding its money, until P4h2 ([below](#p4h2-transfer-fee-posting-2026-10-23)). Sender number/product
ownership is not re-checked at completion (acceptance checked it; neither can move away
while the product lives, and the money, not the number, is what moves).

## P4h2: transfer fee posting (2026-10-23)

Migration `0047` (0045 untouched). TMF678 makes `product` optional on an
AppliedCustomerBillingRate and `appliedBillingRateType` a free string, so a fee needs no
product: `charge_type` gains `oneTime`; `product_id` and `product_offering_id` become
nullable, null exactly for `oneTime`; a unique nullable `balance_action_id` foreign key is
the fee's identity; `abr_fee_scope` and `abr_fee_amount` scope the rules by type (a fee is
positive, over a one-day half-open period, `refunds_rate_id` null). Recurring and refund
rates still require both references; `abr_recurring_amount`, `abr_one_per_start` and
`abr_grant_identity` stay correct because NULLs are distinct. The enum value is compared as
text in the checks (a new value cannot be used where it is added). Downgrade refuses while
a fee exists. Data-model rules: [data-model.md](data-model.md).

`app/worker/prepaid.py` now completes every due transfer. Fee posting is
`app/billing/transfer_fee.py` `post_fee`, the billing-owner path (like the P3g credit):
the transfer command's retained run, one `oneTime` ABR and one `offCycle` bill dated and
covering the posting day, issued then settled from exactly the sources named, the
TMF678 `Create` event, and the run completed, all inside the completion savepoint (one
transaction with the locks of P4h1; any error rolls the fee back with the money and the
retry posts it once). The fee has no rate: `transferCost` is one tax-inclusive Money and
belongs to no price, so it is posted as is (net = transferCost, no tax lines, tax 0)
instead of being split with an invented rate.

Who pays. Originator: the hold (principal plus fee) is released, then its sources are
walked in id order; the first `amount` becomes `funding_consumption` and what remains of
them is allocated to the fee bill (a source straddling the boundary gives a consumption
and an allocation). Receiver: the received `transfer` source (just inserted) is allocated
the fee, so the receiver nets principal minus fee; fee <= principal is already refused at
acceptance (422) and by 0045's check. The fee ABR and bill belong to the payer's account,
the run to the sender's command. Revalidation compares the hold to principal plus an
originator's fee.

Surfaces. TMF678 `appliedCustomerBillingRate` maps `oneTime` to `appliedBillingCharge`
and omits `product` when null (the response is checked against the vendored schema);
the offering name of a fee is `Transfer fee` everywhere (`billing.FEE_NAME`: bill document
line, ledger note, TMF678 `name`). Internal API: a `oneTime` rate is `kind: charge` with
`product` and `productOffering` null (`spec/internal`, nullable); the bill, charges and
ledger queries left-join the offering so the fee is never dropped and `balanceAfter`
keeps matching the balance; a fee covers its one day (`billableDays` = `periodDays` = 1,
not prorated). No mapper treats non-recurring as refund any more. The UI shows
`Transfer fee` for a charge without an offering. The daily run never reads a prepaid
account's ABRs (P3a), so a fee cannot disturb it. Tests:
`tests/test_transfer_completion.py`.

## P4i: balanceActionHistory, events, refunds (2026-10-23)

`GET /balanceActionHistory` and `/{id}` (`app/api/history.py`, no migration) read the same
`balance_action` table without a kind filter, oldest first, so every top-up (including the
one a direct payment records), reservation (owned holds too), adjustment and transfer is
listed exactly once, whatever its status; a reversal is not an action and a cancel only
changes the entry's status. Decisions the spec leaves open:

- The entry id equals its action id; `href` names the history. `@type` is the action's
  subclass (`TopupBalance`...) with `@baseType: BalanceActionHistory`, so the kind is
  readable without an extra attribute. The body is the action's own body, so every
  attribute the action returns appears and nothing is invented.
- The one schema exception is unchanged: `receiverLogicalResource` (required by the schema)
  is given only by a transfer, which takes it in its request. The test client validates
  history entries with `contract.balance_history_errors(kind)`, status stays required.
- Filters: `id`, `status`, `bucket.id`, `receiverBucket.id`, `partyAccount.id`; paging and
  `fields=` as elsewhere. Read-only: the spec defines no write.
- The gap lock loses the two "endpoint absent" lines and `PENDING` is empty; the attribute
  probe adds the vouchers/auto-top-up attributes and the nested echoes of client references
  (channel, logicalResource href/name, bucket name) as accepted deviations.

Events: all twelve names were registered since P4c; P4i adds a test that sends eleven
(Create, Failure and Cancel of top-up, adjustment and reservation; Create and Failure of
transfer) and checks every stored payload against its listener schema. The twelfth,
`TransferBalanceCancelEvent`, stays registered but is never sent (a transfer is final).

Refunds: a cash refund was already capped by the reconciled spendable balance, which
excludes held funds (`create_refund` takes the minimum of the sources and
`funding.load(...).spendable`); P4i pins it at the API with an operator hold: with 50
topped up and 30 reserved, 21 is a 409 and 20 is accepted. Refunds stay a recorded
TMF676 refund, not a balance action, and so are not in the history (plan: "Cash refund").

The MCP registry must name every operation, so P4i adds the read tools `list_history` and
`get_history` (184 tools, 85 with read scope); the remaining prepaid tooling is P5b.

## P5a: enable prepaid accounts (2026-10-23)

TMF666 POST and PATCH accept `ratingType: prepaid`; the 422 gate is gone. No migration
(`0034` already holds the mode, the bucket and the history trigger).

- **Creation.** POST inserts the account with its mode and, in the same transaction, its
  one `prepaid_bucket` (the TMF654 bucket and accumulatedBalance identities). An absent
  `ratingType` is `postpaid`; `null` stays a 422 on PATCH.
- **creditLimit.** A positive value on a prepaid account is a 422 on POST and PATCH (zero
  and null are fine); switching to prepaid with a positive stored limit is a 422 unless the
  same PATCH removes it. The currency still defaults from a limit's unit.
- **Switching mode.** PATCH may move postpaid to prepaid only on an account with no history
  (`parties.ACCOUNT_HISTORY` counts, as DELETE does); anything else is a 409. Decision the
  plan left open: the bucket is history and is immutable, so a prepaid account can never go
  back to postpaid (409); a repeat of the current value is a 200. Because the bucket
  counts as history, a prepaid account cannot be deleted either (close it).
- **References.** `RatedBillingAccountRef.ratingType` accepts both values and must equal
  the referenced account's mode (422 otherwise), checked on order POST (order, items and
  nested items), draft replace, product POST and PATCH, and on-demand bill POST.
  `account_ref` answers the stored mode in every TMF622/637/678 body.
- **Internal preview.** `firstCharge` on a prepaid account does not run the postpaid core
  (which bills nothing for prepaid): the first interval starts on `startDate`, is paid in
  full with its taxes (`IntervalQuote`, the same quote a purchase holds), `prorated` is
  false, `next` null, `chargeRunDate = billDate = max(startDate, today)`, `balance` the
  ledger, `balanceAfter` ledger minus the interval, `exceedsCreditLimit` means
  shortfall > 0. It adds optional `required`, `available` (spendable, never below zero) and
  `shortfall` Money fields, absent on postpaid; the OAS lists them as optional so the
  postpaid contract and the generated types stay valid. The funds shown are today's even
  for a future start (a future order holds nothing).

Tests: `tests/test_api_prepaid_accounts.py`. TEST-CHANGE: the two tests that asserted the
gate (`test_rating_type_is_postpaid`, the `ratingType: prepaid` validation row) now assert
the new contract.

## P5b: MCP (2026-10-23)

See [mcp.md](mcp.md#p5b-mcp-for-prepaid-2026-10-23): the six journaled POST tools take an
optional `idempotency_key` (the `Idempotency-Key` header); `make mcp-smoke` records one
top-up and one future-start purchase locally.

## IMSI (issue #66)

The IMSI is a second TMF639 `LogicalResource` beside the MSISDN, assigned and changed by
TMF622 orders exactly like the subscriber number (plan: issue #66). Out of scope: port-in/out,
physical SIM inventory, network provisioning.

- **Resource.** Built-in specification `imsi` (fixed reference, no href). `value` is
  `^[0-9]{14,15}$`, unique per specification. The optional ICCID is the `resourceCharacteristic`
  `{"@type": "StringCharacteristic", "name": "iccid", "valueType": "string", "value": "89..."}`
  (`@type` is required by the vendored TMF639 schema), `^89[0-9]{16,18}$`, unique over IMSI
  resources, 422 on an MSISDN, changeable only while the IMSI has never been assigned (409
  after). It lives in the `iccid` column and is rendered back as the characteristic; other
  characteristics stay documents. Filter `resourceSpecification.id` lists either kind.
- **Released IMSI reuse is not automatic.** Accepting an order that releases an IMSI
  (termination, IMSI change) sets its `administrativeState` to `locked` in the same
  transaction. The assignment is untouched, so a future-dated release works until its date,
  and cancelling the releasing order leaves the IMSI locked but assigned. An operator may
  PATCH it back to `unlocked`. No quarantine for IMSI.
- **Ordering.** A specification listing `imsi` requires exactly one IMSI
  (`product.requires_imsi`, snapshotted like `requires_msisdn`, frozen once an offering using
  the specification has order history). `realizingResource[]` holds at most one reference per
  kind; the server resolves the kind from the inventory. Stored rejections: `imsiRequired`,
  `imsiUnavailable`, `imsiNotRequired`, `realizingResourceDuplicate`; MSISDN codes unchanged.
  A reference the inventory does not know is attributed to the first required kind still
  missing.
- **Modify** carries the complete resulting set: a member equal to the product's current one
  of its kind is unchanged, a different one is a change, a required kind left out is
  `...Required`, and a set that changes nothing is `...Unavailable` ("already uses"). MSISDN
  and IMSI may change in one item; offering-change, prepaid timing and status exclusivity
  follow the MSISDN rules. Only changed kinds release and claim.
- **Data.** Migration `0049`: `subscriber_number` kind and value checks, `UNIQUE(kind, value)`,
  `UNIQUE(id, kind)`, `iccid`; `number_assignment.resource_specification_id` (default
  `msisdn`) with a composite foreign key and the product exclusion per kind;
  `product.requires_imsi`. Downgrade refuses while an IMSI or an IMSI-requiring product exists.
- **Balances** (`logicalResource` on buckets and top-ups) list MSISDNs only.

Tests: `tests/test_imsi.py`.
