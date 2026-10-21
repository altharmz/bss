# Prepaid subscriptions: finalized scope

Status: **design complete**, 2026-10-20. The human authorized completing remaining
choices according to TMF and committing/pushing the design. This scope and
[the finalized implementation design](prepaid-implementation.md) are the hand-off
for a separate development session. The latter contains pinned contract references,
explicit deviations, exact lifecycle/recovery rules and API/UI examples; it takes
precedence over earlier proposal wording. No runtime implementation or deployment
is included in the design commit. Existing accounts stay postpaid.

## First release

Two parts, delivered in this order:

1. **Subscriber numbers (MSISDN)** for every product that needs one, prepaid or
   postpaid: a number inventory, assignment by orders, lookup by number.
2. **Prepaid recurring subscriptions** alongside postpaid accounts. An operator
   records money received, and a product can start or renew only when the account can
   fund its next service interval, including tax. Unfunded intervals create no debt.
   Support the existing daily, weekly, monthly and yearly prices and their
   multipliers. Balance transfers between subscriber numbers are included.

Example: an account has USD 50 available and renews a USD 30 tax-inclusive plan.
The successful renewal leaves USD 20 available and grants the next paid interval.
At the following renewal, USD 20 cannot fund USD 30: service entitlement ends at the
paid-through boundary, no new charge is posted, and the account needs a top-up.
After enough money arrives, renewal starts a new interval from the resumption date;
the unfunded gap is neither charged nor granted retrospectively.

This is subscription prepaid. Usage charging, data/minute/SMS bundles, vouchers,
promotional balances, automatic top-up and card collection, external payment
processing, number portability and network provisioning integrations are outside the
first release. The BSS records numbers, funding and service entitlement; actually
activating, barring or restoring a number in the network requires a separate
provisioning integration.

## TMF basis

The project's rule is to implement what the vendored specs define and skip the rest
([follow TMF](decisions/tmf-api.md)). None of the seven vendored APIs defines a number
inventory, a prepaid balance, a top-up or a reservation. TM Forum defines both, so the
design is to **vendor two more specs** under `spec/` (pinned commit, blob SHA,
`SHA256SUMS`, enum lock, gap probe), as was done for TMF676 v4:

| Spec | File | Why |
|---|---|---|
| TMF639 Resource Inventory v5.0.0 | `TMF639-Resource_Inventory_Management-v5.0.0.oas.yaml` | An MSISDN is a `LogicalResource` whose `value` holds the number (the spec's own example: "'0746712345' for MSISDN's"). TMF622/637 already reference it as `realizingResource`. |
| TMF654 Prepay Balance Management v4.0.0 | `TMF654-PrepayBalance-v4.0.0.swagger.json` | Stable contract for prepaid balances, top-ups, reservations, adjustments and transfers. v5 exists as a preview; this release uses stable v4. Its actions identify subscribers by `LogicalResourceRef` ("eg MSISDN"). |

Both were read upstream on 2026-10-20 (`tmforum-apis/TMF639_ResourceInventory`,
`tmforum-apis/TMF654_PrepayBalanceManagement`); every name below is re-checked against
the vendored files in stage 1. Money and Quantity values are JSON numbers in both,
read and written as `Decimal` exactly like TMF676 v4 (`tmfjson`).

Not vendored, deliberately: TMF685 Resource Pool (number reservation for a basket) and
TMF634 Resource Catalog. Their role is covered below by a fixed built-in resource
specification and by allocation at order completion, the same way the built-in billing
cycle specification stands in for a catalog today.

### Subscriber numbers: TMF639 mapping

| Element | Behavior |
|---|---|
| The number | `resource` with `@type: LogicalResource`, `value` the MSISDN in E.164 digits without `+` (unique, validated: 8-15 digits), `name` `MSISDN <value>`, `resourceSpecification` the built-in `msisdn` specification. |
| Operations | All five: list (filters include `value`, `usageState`, `administrativeState`), retrieve, create (operator adds a number to the inventory), PATCH (descriptive attributes, `administrativeState`), DELETE (only a number never assigned to a product, like the other delete rules), plus `/hub` and its four events. |
| States | `administrativeState`: `unlocked` (assignable) / `locked` (withheld by the operator). `usageState`: `idle` (free) / `busy` (realizing a live product), set by the server only. TMF639 defines `active` as having spare capacity for additional users; an exclusively assigned MSISDN has none. `allocationStatus` mirrors assignment (`available`/`allocated`); `operationalState` stays `enabled`. Any other client-set state is a 422. |
| Requirement | A TMF620 product specification that lists the `msisdn` resource specification in `resourceSpecification[]` makes a number mandatory for its products. Others take none; existing specifications are unchanged. |
| Assignment | TMF622 `add` item: `product.realizingResource` = a `ResourceRef` to an `idle`, `unlocked` number. Missing, unknown, locked or busy numbers reject the order (`msisdnRequired`, `msisdnUnavailable`), stored like other rejections. Assigned when the product's order completes; one live product per number (database constraint). No auto-pick: TMF622 carries the operator's choice. |
| Number change | TMF622 `modify` item with a new `product.realizingResource`, effective `requestedStartDate`; billing is unaffected. |
| Release | At effective termination/cancellation the number returns to `idle` unless another accepted assignment starts then. Reuse observes `MSISDN_QUARANTINE_DAYS`, integer >= 0, **default 0** (the human's choice, 2026-10-20). Quarantine is counted in `BILLING_TZ` dates from stored release date; an ineligible assignment is rejected `msisdnUnavailable`. New checks use the current setting; accepted future claims are grandfathered as specified in the implementation design. New setting: `app/config.py`, `.env.example`; production compose changes require separate harness approval. |
| Lookup | TMF639 `GET /resource?value=` finds the number; TMF637 `GET /product?realizingResource.id=` finds its product. TMF637 product bodies return `realizingResource` with `name`. |

### Prepaid balances: TMF654 mapping

| Resource | Operations and behavior |
|---|---|
| `bucket` | list, retrieve. One `monetary` bucket per prepaid billing account and currency, derived from the ledger: `remainingValue` (spendable), `reservedValue`, `status` `active` / `suspended` (account Suspended) / `expired` (account Closed), `partyAccount`, `product[]` and `logicalResource[]` (the account's live products and numbers), `isShared` true when it funds more than one number. |
| `accumulatedBalance` | list, retrieve. Per account and currency: `totalBalance` over its buckets (one today). |
| `topupBalance` | All five operations. Create needs `amount`, `usageType: monetary`, `bucket`, `partyAccount`; `logicalResource` may name the subscriber, checked to belong to the account. Creates its TMF676 payment in the same transaction. |
| `reserveBalance` | All five. Server-created for each pending start and renewal (owned by the order or renewal); also client-created as an operator hold on funds (`validFor.endDateTime` optional; expired holds are released by the worker). |
| `adjustBalance` | All five. Operator credit (positive amount) or debit (negative), `adjustType: oneTime`. |
| `transferBalance` | All five operations, with immutable monetary fields after acceptance. POST reserves funds and returns `created`; the billing worker moves principal and posts any fee atomically, then marks `completed`. Transfers are final; corrections are new opposite transfers. `transferCost` is charged to `costOwner` (`originator` default / `receiver`) with a settled bill. Same-account transfers are `failed` (`sameBucket`). |
| `balanceActionHistory` | list, retrieve. Every top-up, reservation, adjustment and transfer, with the same ids. |
| `/hub` + 12 event types | Create, cancel and failure names of the four actions, through the existing outbox ([events](decisions/events.md)); transfer cancel is subscribable but unreachable. Listeners are implemented by subscribers, not inbound BSS routes. |

Action lifecycle (`ActionStatusType`), consistent with stored TMF622 rejections:
validated actions are stored; transfers use a durable worker command.

- Business refusal (insufficient funds, account not prepaid, number not live,
  `sameBucket`): stored `failed` with `reason`, `FailureEvent`, 201. Malformed or
  unknown input stays a 400/422 with nothing stored.
- Top-up and adjustment: `completed`. Transfer: `created` until the worker commits
  its financial result, then `completed` (or `failed` if revalidation refuses it).
  Reservation: `created` while it holds
  funds, `completed` when consumed by its charge, `cancelled` when released.
- `PATCH status: cancelled` reverses a completed top-up or adjustment if the
  money is still unspent and unreserved on the side that received it (409 otherwise),
  with a `CancelEvent`; on a held reservation it releases it. A reservation owned by an
  order or renewal is cancelled only through that order (409 here). Other PATCHes
  change descriptive attributes only, subject to each v4 PATCH schema. Transfer PATCH
  has no status field; use a new opposite transfer to correct it. Original money
  records are immutable; eligible cancellations append compensating entries.
- `DELETE` removes only `failed` actions, as DELETE removes only rejected orders.

Accepted deviations are recorded during implementation with reasons in
`tests/tmf-gaps.txt`; the detailed design lists the additional contract restrictions
and the conditional history response exception. The business scope exclusions are:

- `usageType` other than `monetary` (voice, data, sms, other): bundles are out of
  scope; 422.
- `isAutoTopup: true`, `recurringPeriod`, `numberOfPeriods`: need payment collection,
  which this BSS does not do; 422.
- `voucher`: no voucher management API is vendored; 422.
- `adjustType: recurring`: recurring charges come from TMF620 prices; 422.

### Mapping onto the existing APIs

| Spec element | Prepaid meaning |
|---|---|
| TMF666 `BillingAccount.ratingType` | `prepaid` or `postpaid` (today: always `postpaid`, anything else 422). Settable on POST; PATCH only before financial or product history (409 otherwise). |
| TMF622/637/678 `BillingAccountRef.ratingType` | Accepted when it equals the referenced account's value, as `postpaid` is today; a mismatch stays a 422. |
| TMF666 `creditLimit` | A prepaid account may carry none or zero; a positive value is a 422 (an overdraft this mode cannot honour, the reasoning that refuses `ratingType: prepaid` today). |
| TMF666 `accountBalance[]` | Unchanged entry: one `currentBalance` per currency, now payments + adjustments ± transfers - ABRs. Spendable and reserved amounts live on the TMF654 bucket, not in invented `balanceType`s. |
| TMF676 `payment` | Still the record of money received. A top-up creates its payment; a payment posted directly to a prepaid account is recorded as a `completed` `topupBalance` too, so `balanceActionHistory` is complete whichever API the operator used. |
| TMF622 order, insufficient funds | `rejected` with `productOrderErrorMessage` code `insufficientFunds` (required and available amounts in `reason`), like every other rejection code. TMF637 POST product keeps its 409-with-reasons rule. |
| TMF622 order, funded immediate start | `inProgress` (item `inProgress`) while the worker authorizes it, then `completed`. "Never reached" today because orders complete synchronously; prepaid makes it reachable and the decision record changes with it. |
| TMF637 product, pending start | `pendingActive` until the worker posts the charge and grants the interval (also "never reached" today). The number is held for it meanwhile. |
| TMF637 product, funds exhausted | `suspended` from the paid-through boundary, back to `active` on the funded renewal. TMF637 has one `suspended` status, so the cause (operator order or funding) is internal and visible on the bucket and in `balanceActionHistory`; a top-up lifts only a funding suspension. The number stays assigned. |
| TMF678 ABR | Unchanged shape; `periodCoverage` is the funded interval (its end is the paid-through date), `isBilled: true` with the bill. Termination credits are ABRs as postpaid refunds are. |
| TMF678 customerBill | `runType: offCycle`, `state: settled` from the reserved sources in the same transaction. Monthly `onCycle` invoicing stays postpaid-only. |
| TMF620 price | Unchanged; prepaid is the account's mode, not the price's. `priceType` stays `recurring` (free text; the spec defines no prepaid value). |

## What the current system requires us to change

- No vendored spec has a number inventory or prepaid balance resources; TMF639 and
  TMF654 must be vendored, with new base paths, routers, MCP tools and gap lines.
- `realizingResource` is stored today as an unchecked document on products. It becomes
  a checked reference to the number inventory for products whose specification needs
  a number; other products keep today's behavior.
- TMF666 `ratingType` describes prepaid/postpaid in the vendored contract, but the
  implementation only accepts and returns `postpaid`, including account references
  in orders, inventory and bills.
- Charges are already calculated in advance. Today their posting is independent of
  available funds; credit limits are informational. Prepaid must enforce funding.
- Only billing runs write charges and bills. The daily worker sees orders through
  the preceding midnight. Immediate prepaid authorization needs an additional worker
  command path; the postpaid cutoff and replay rules must remain unchanged.
- Orders complete synchronously, so TMF622 `inProgress` and TMF637 `pendingActive`
  are never reached ([tmf-api.md](decisions/tmf-api.md)). Prepaid starts are
  asynchronous and use them; the recorded "never reached" lists change.
- Billing follows product dates even when a product is suspended. Prepaid must use
  explicit funded service intervals; setting `suspended` alone cannot stop charges,
  and a funding suspension must not be confused with an operator's.
- The derived balance gains two entry kinds (adjustments, transfers). They are ledger
  entries, not ABRs, so "only the billing run creates ABRs" holds; transfer costs are
  charges and go through billing.
- Settlement and refunds consume particular payment/credit sources. Prepaid funding,
  adjustments and transfers must share their locking and source bounds, rather than
  introduce a second wallet. The TMF654 bucket is a view of those sources.

Relevant foundations: [accounts](decisions/accounts.md),
[billing](decisions/billing-core.md), [charge periods](decisions/charge-periods.md),
[worker](decisions/worker.md), [settlement](decisions/bills.md),
[refunds](decisions/billing-completion.md), [events](decisions/events.md),
[catalog](decisions/catalog.md) and [TMF conventions](decisions/tmf-api.md).

## Business rules

| Choice | Proposed first-release behavior |
|---|---|
| Where prepaid is selected | On the billing account: TMF666 `ratingType = prepaid` or `postpaid`; one mode per account. A customer can own accounts of both kinds. |
| Existing accounts and products | Remain postpaid and number-less, with unchanged money, history and behavior. |
| Switching mode | Allowed only before financial/product history exists. Moving a live account is a separate future workflow. |
| Numbers | Required where the product specification says so, in both modes; assigned by the order, released on termination. |
| Funding | TMF654 `topupBalance` (`monetary`), backed by a TMF676 payment; direct TMF676 payments count as top-ups. No overdraft, credit-limit allowance or grace period. |
| Spendable funds | The bucket's `remainingValue`: derived balance less held reservations (`reservedValue`), reconciled with unused payment/credit sources; include unbilled charges and recorded payouts. Do not subtract bill allocations twice. |
| First period | Full interval from activation, including monthly length 1. Postpaid keeps its existing calendar/proration rules. |
| Renewal | Automatic, a full interval at the applicable price plus tax, only when fully funded. Price changes apply at renewal. |
| Insufficient initial funds | The order is `rejected` with `insufficientFunds`. No charge, reservation, number assignment or entitlement. |
| Insufficient renewal funds | Stop extending the funded interval; the product becomes `suspended` (funding cause) at its paid-through boundary. Record `needs top-up` as a business outcome, not a broken billing run. |
| Top-up after expiry | Queue resumption; start a full interval on its successful authorization date (product back to `active`). Never bill the unfunded gap. |
| Future starts | Reserve at the due start, not months ahead. A scheduled order does not guarantee future funds; at the due date an unfunded start leaves the product `pendingActive` with a funding shortfall rather than failing the order retrospectively. |
| Several products on one account | Fund independently in deterministic due-date order, then a stable product tie-breaker. Show this ordering; do not silently choose by query order. |
| Manual suspension | Internally separate from a funding suspension although both are TMF637 `suspended`. It does not refund a paid interval; a top-up never overrides an operator suspension or a Closed account. |
| Termination | Credit unused paid business dates using the original charge/tax rounding rules (credit ABR). Release held reservations exactly once; release the number. |
| Transfers | Between prepaid accounts in the same currency, limited to spendable funds including the fee; final, corrected through a new opposite transfer. Never across modes or currencies. |
| Adjustments | Operator goodwill credits and corrective debits; a debit cannot exceed spendable funds. |
| Cash refund | Continue recording a completed TMF676 refund, limited to funds left after reservations and existing obligations. Refunds never initiate an external transfer. |
| Offering changes | Initially permit changes at the next prepaid renewal. Defer mid-period prepaid upgrades/downgrades and their extra reservation/refund rules. |
| Documents | A successful prepaid purchase/renewal (and a transfer cost) produces an `offCycle` bill, `settled` from its funding sources in the same transaction. Keep monthly invoicing unchanged for postpaid. |
| Events | TMF639 resource events; TMF654 create/cancel/failure events; TMF637 state changes for `pendingActive`/`active`/`suspended`; TMF622 order state changes. All through the existing outbox. |

## Transaction and worker design

1. Keep financial amounts as Decimal and ledger-derived balances. Add persisted
   numbers and their assignments, account mode, funding reservations (served as
   `reserveBalance`), adjustments, transfers, funded product intervals and a durable
   prepaid command/result journal (served as `balanceActionHistory`). Store quoted
   price, tax, interval, effective date and idempotency identifiers needed to explain
   and replay every decision.
2. Serialize every money-consuming operation on the account row. A transfer locks both
   accounts in id order. Payment allocation, payout, adjustment, transfer, prepaid
   reservation and final posting must all honor reservations. Database constraints
   protect source totals, duplicate consumption, overlapping grants and one live
   product per number.
3. Immediate-start authorization reserves the exact tax-inclusive amount
   (`reserveBalance` `created`), holds the number and queues work atomically with the
   order, which stays `inProgress` and its product `pendingActive`. The API does not
   write a charge. The billing owner posts the charge, issues/settles its bill,
   completes the reservation and grants the paid interval in one transaction, then
   completes the order.
4. Extend billing-run storage to identify prepaid command runs separately from the
   unique daily postpaid run. Keep all financial entries attached to a real run; audit
   every daily-run query so command runs cannot become a daily scheduling/retry anchor.
   Existing daily run IDs, dates and history remain intact.
5. Use the existing worker ownership mechanism for prepaid commands, due renewals and
   expired holds. Process eligible work on its normal wake-up (currently every 60
   seconds), with bounded API/UI polling. Expose pending, funded, needs-top-up and
   technical-failure outcomes distinctly. A missing price remains an error, not an
   insufficient-funds case.
6. Persist paid-through dates independently of worker liveness. Entitlement expires
   at that boundary even if renewal processing is late. A worker outage cannot grant
   unpaid service; recovery never treats a later top-up as money available in the past.
   Pin the exact due-boundary and catch-up ordering in tests before implementation.
7. Persist commands and reservation transitions so retries, process death, deploys and
   duplicate API submissions produce at most one charge and one grant. Reconcile
   stranded reservations through the owning command; never silently discard them.
8. Apply changes to funded intervals through the approved order/refund workflows.
   Preserve the rule that orders create/end products, while billing owns charges.
   Funding status must not bypass a manual suspension or termination.

The concurrency model and run extension are essential scope, not optional polish.
Reservations alone are insufficient if existing payouts or settlement can ignore them.

## Delivery stages

| Stage | Reviewable result |
|---|---|
| 1. Design and contracts | Design is complete in this scope and the linked implementation hand-off. Development begins by vendoring the pinned TMF639/654 contracts, provenance checks, enum/gap accounting and executable API fixtures. Add progress scope when implementing; design completion does not mean contracts are vendored or runtime coverage exists. |
| 2. Subscriber numbers | TMF639 resources, the built-in `msisdn` specification, `realizingResource` assignment/change/release through TMF622, lookups, events, MCP tools, UI number inventory and number picker in New subscriber. Works for postpaid; shippable on its own. |
| 3. Schema and funding core | Additive migrations: account mode, reservation/source accounting, adjustments, transfers, paid intervals and command-run identity. Existing rows default to postpaid. Prove concurrency and rollback with existing populated financial history, including immutable refunds. |
| 4. API and worker | TMF666 `ratingType: prepaid`; TMF654 resources and hub; order/product states for pending activation and funding suspension; renewal, expiry/resumption, termination credits, transfers, settled documents and durable recovery. Update TMF account references, internal previews, MCP tools and the gap lock together. Preserve postpaid behavior. |
| 5. Admin UI | Account mode selector/filter; available, reserved and paid-through figures from the bucket; Record top-up, adjustment and transfer (by number); funding status and renewal shortfall; pending activation/resumption; clear refusal and retry messages. Pin request bodies in frontend and backend tests. |
| 6. Release validation | Mixed prepaid/postpaid demo data with numbers; full backend/frontend/browser checks; TMF639/654 bodies validated against the vendored specs; documented operational recovery and migration rollback limits. Shipped through the normal cycle. |

Stages 3 and 4 are developed together behind an unavailable prepaid create path
(`ratingType: prepaid` stays a 422) until money accounting and worker recovery are
complete. Do not expose partially working prepaid accounts merely because the
database can store the mode.

## Acceptance criteria

- Numbers: a duplicate or malformed `value` is refused; a locked, busy or unknown number
  rejects the order; a number realizes at most one live product; termination and
  cancellation return it to `idle`; a modify item moves a product to a new number;
  products of specifications without `msisdn` behave exactly as today. With
  `MSISDN_QUARANTINE_DAYS=0` a released number is assignable the same day; with N > 0
  it is rejected through release date + N - 1 and assignable on release date + N.
- Exact funds permit a purchase, one cent short rejects it (`insufficientFunds`), and
  tax is included in both decisions. Zero-priced periods work without requiring a
  positive balance.
- Two concurrent starts cannot overspend; payouts, adjustments, transfers and
  explicit/automatic settlement cannot consume reserved sources. Concurrent top-ups,
  transfers in both directions and renewals serialize without deadlock.
- Worker death at each transition leaves either no financial change or one complete
  charge, settled bill and funded interval. Retries and duplicate commands cannot double-charge.
- Exhaustion stops future funded service without accruing debt. Top-up resumes only
  eligible products and does not fund an earlier gap or override manual suspension.
- Cancelling a top-up or positive adjustment whose original source was spent or held
  is a 409; an eligible cancellation restores the balance exactly. Transfers cannot
  be cancelled; a compensating opposite transfer is a new action with current checks.
- Test all supported cadences, month ends, leap years, DST and midnight boundaries;
  several products with insufficient shared funds; scheduled orders; price/tax changes;
  zero prices; termination credits; refunds; and recovery after missed renewals.
- A delayed worker, historical replay and a top-up received after expiry have explicit,
  consistent results. Prepaid command runs do not alter daily-run scheduling or counts.
- Every TMF639 and TMF654 response validates against the vendored schema, with the
  single documented conditional exception for non-transfer history receivers; bucket
  figures equal the ledger and source allocations; `balanceActionHistory` lists every
  action exactly once. Order, product and bill states match the mapping tables.
- Existing postpaid cases retain their current results. Mixed-mode customers and
  financial-account balances reconcile with the ledger and source allocations.
- New migrations preserve existing account/product/payment/bill/refund history and
  database protections. Downgrade refuses numbers or prepaid history that older code
  cannot represent.

## Final design decisions

Decisions finalized under the human's authorization to choose according to TMF:

1. Vendor TMF639 Resource Inventory v5.0.0; an MSISDN is a `LogicalResource`, required
   by product specifications that reference the built-in `msisdn` resource
   specification, assigned through TMF622 `realizingResource`, in both modes.
2. Vendor TMF654 Prepay Balance Management v4.0.0 and implement all its operations,
   including `transferBalance` and client-created reservations; accepted scope and
   contract deviations are enumerated in the implementation design.
3. TMF654 actions are stored even when refused (`failed`, failure event), deleted
   only when failed; top-ups/adjustments and operator holds support eligible
   cancellation. Transfers follow their v4 schema and are final.
4. Account-level mode via TMF666 `ratingType`; one shared monetary bucket per prepaid
   account and currency; full-period prepayment with zero overdraft/grace.
5. Asynchronous starts using TMF622 `inProgress` and TMF637 `pendingActive`; refusals
   as `rejected` orders with `insufficientFunds`; funding exhaustion as TMF637
   `suspended`.
6. Prorated termination credits; renewal-boundary offering changes; an `offCycle`,
   `settled` bill per successful prepaid purchase and per transfer cost.

Decided by the human (2026-10-20):

7. Number quarantine after release is a setting, `MSISDN_QUARANTINE_DAYS`, default 0.
8. Subscriber numbers (stage 2) ship on their own, before the prepaid stages are
   complete; they ship through the normal cycle (commit, push, deploy, no approval).

9. Late recovery buys one interval from today's authorization date, never historical
   catch-up, even with older funds. Entitlement cannot outlive its persisted grant.
10. Optional Idempotency-Key provides durable client retry identity; UI and MCP carry
    it explicitly. Internal command identity independently prevents double posting.
11. Reservations require an explicit amount; missing top-up payment method records
    an unspecified receipt. No guessed cash payment, all-funds hold or fictitious
    history receiver is created.

Design completion and its commit/push are authorized in this session. Development
starts in another session; implementation commits follow CLAUDE.md's development cycle
(no approval). Deployment only when the human asks (2026-10-21).
