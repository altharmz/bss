# Prepaid subscriptions and subscriber numbers: the design as built

Built (migrations `0032`-`0047`): subscriber numbers first, then the prepaid funding core. This is the one design record for both; it replaces the earlier scope plan
and implementation plan. The decisions as built, with their deviations, are in
[decisions/prepaid.md](decisions/prepaid.md); the money rules in
[decisions/billing-core.md](decisions/billing-core.md); recovery and rollback limits in
[operations.md](operations.md#prepaid-recovery-and-rollback). Existing accounts stay
postpaid. Not built, and tracked as issues: moving an account with history between modes
(#150), a mid-period prepaid offering change (#151), overdue bills (#152).

## Scope

1. **Subscriber numbers (MSISDN)** for every product that needs one, prepaid or postpaid:
   an inventory, assignment by orders, lookup by number.
2. **Prepaid recurring subscriptions** beside postpaid accounts. An operator records money
   received; a product starts or renews only when the account can fund its next service
   interval including tax. Unfunded intervals create no debt. Daily, weekly, monthly and
   yearly prices and their multipliers are supported. Balance transfers between subscriber
   numbers are included.

Example: an account has USD 50 available and renews a USD 30 tax-inclusive plan. The renewal
leaves USD 20 and grants the next paid interval. At the following renewal USD 20 cannot fund
USD 30: entitlement ends at the paid-through boundary, no charge is posted, and the account
needs a top-up. After a top-up, renewal starts a new interval from the resumption date; the
unfunded gap is neither charged nor granted retrospectively.

This is subscription prepaid. Usage charging, bundles, vouchers, promotional balances,
automatic top-up and card collection, external payment processing, number portability and
network provisioning are out of scope. The BSS records numbers, funding and entitlement;
activating, barring or restoring a number in a network needs a separate provisioning
integration.

## TMF basis and contract pins

The rule is to implement what the vendored specs define and skip the rest
([tmf-api.md](decisions/tmf-api.md)). None of the first seven vendored APIs defines a number
inventory or a prepaid balance, so two more are vendored under `spec/` (pinned commit, blob
SHA, `SHA256SUMS`, enum lock, gap probe), unmodified:

| Contract | File | Git blob SHA |
|---|---|---|
| TMF639 Resource Inventory v5.0.0 (an MSISDN is a `LogicalResource`) | `TMF639-Resource_Inventory_Management-v5.0.0.oas.yaml` | `2c34d9772bb937e4aebb59ae71d1a6825df39737` |
| TMF654 Prepay Balance Management v4.0.0 (stable; v5 is a preview) | `TMF654-PrepayBalance-v4.0.0.swagger.json` | `60378955ca383e0f2e6427d57d9d9d5ed1ddb349` |

Upstream: `tmforum-apis/TMF639_ResourceInventory` (commit `2693a85…`) and
`tmforum-apis/TMF654_PrepayBalanceManagement` (commit `562e655…`). TMF685 Resource Pool and
TMF634 Resource Catalog are not vendored: a fixed built-in `msisdn` resource specification
and allocation at order completion stand in for them, as the built-in billing cycle
specification stands in for a catalog.

Findings that shape the code:

- TMF639's server variable defaults to `https://serverRoot/resourceInventory/v5`; the base
  path is `/tmf-api/resourceInventory/v5` (the base-path probe resolves the variable
  default). TMF654's base is `/tmf-api/prepayBalanceManagement/v4`; its served operations
  are listed in the generated [API reference](api/README.md).
- Assigned numbers are `busy`: the spec defines `active` as having spare capacity for more
  users. `allocationStatus` is a free string, which allows `quarantined`.
- Quantity (`amount`, `units`) carries action amounts and bucket figures; transfer cost is
  Money (`value`, `unit`). Both are read and written as exact `Decimal` JSON numbers.
- Transfer POST requires reason, receiverLogicalResource, channel, logicalResource, amount,
  usageType, bucket, receiverBucket and receiverBucketUsageType. Adjustment POST has no
  partyAccount. Reservation POST requires usageType, bucket and partyAccount; the amount is
  optional in the schema but **required here** (422), because there is no usage request to
  infer a hold from. Top-up POST requires amount, usageType, bucket and partyAccount; an
  omitted `paymentMethod` generates a payment labelled `Unspecified recorded receipt` (no
  external method id, no implication of cash); a provided one needs an `id`.
- The retry, accounting, calendar and recovery choices below are local policies where the
  contracts give no algorithm. They are not conformance claims.

### Deviations

- **Transfers are final.** `TransferBalance_Update` has no status. Corrections are new
  opposite transfers, subject to current funds; they do not erase the original or refund its
  fee. Transfer PATCH takes the four required fields, permits reason/channel/requestor
  changes, and requires monetary and resource fields to equal the stored ones (409).
  `TransferBalanceCancelEvent` is subscribable but never emitted. DELETE removes failed
  actions only.
- Other PATCHes follow their schema: top-up and adjustment require status, reservation
  requires status and reason. Repeating the current status permits descriptive edits;
  `cancelled` reverses a reversible credit or debit or releases a client hold. Amount,
  currency, ownership and sources cannot be rewritten (409); server-managed dates and status
  accept equal values only. v4 PATCH consumes `application/json`.
- `BalanceActionHistory` wrongly requires `receiverLogicalResource` even for actions that
  have none. History returns a truthful projection (original id, href, status, applicable
  fields; no fictitious number). The single response-schema exception removes that required
  key for non-transfer entries; transfers validate against the unchanged schema. Reservations
  are included. Required status is never relaxed.
- All accepted restrictions are in `tests/tmf-gaps.txt`: `usageType` other than `monetary`
  (voice, data, sms, other), `isAutoTopup: true`, `recurringPeriod`, `numberOfPeriods`,
  `voucher` and `adjustType: recurring` are 422; the reservation amount is explicit; start of
  `reserveBalance.validFor` must be absent or not in the future; executed-action monetary
  fields are immutable; only the number resource subclasses and states below exist; the
  history exception above.
- Action defaults: `validFor` on top-up, adjustment and transfer is descriptive audit
  metadata, not delayed execution. `reserveBalance.validFor.endDateTime` expires the hold.
  The Quantity default `amount=1` is never used. `adjustType` defaults to `oneTime`,
  `isAutoTopup` to false. Method, channel and requestor metadata are stored as sent and
  schema checked, not resolved against unvendored catalogs; references must agree, and an
  optional top-up `logicalResource`/`product` must belong to the bucket's account.
- Listeners are subscriber endpoints; the BSS sends events and adds no listener routes.

## Subscriber numbers: TMF639 mapping

| Element | Behavior |
|---|---|
| The number | `resource` with `@type: LogicalResource`, `value` the MSISDN as E.164 digits without `+` (8-15 digits, first digit 1-9, unique over the whole value, stored as text, never silently stripped), `name` `MSISDN <value>`, `resourceSpecification` the built-in `msisdn` specification (a fixed reference, no href). |
| Operations | All five (list with `value`, `usageState`, `administrativeState` filters; retrieve; create; PATCH of descriptive attributes and `administrativeState`; DELETE only for a never-assigned number) plus `/hub` and its four events. |
| States | `administrativeState` `unlocked` (assignable) / `locked` (withheld by the operator). `usageState` `idle` / `busy`, set by the server only. `allocationStatus`: `reserved` (future claim), `allocated` (current), `quarantined`, `available`. `operationalState` stays `enabled`. Any other client-set state is a 422. |
| Requirement | A TMF620 product specification that lists the `msisdn` resource specification in `resourceSpecification[]` makes a number mandatory for its products. The requirement is resolved at ordering and snapshotted on the product; it may be added or removed only while no offering using the specification has order history (409). |
| Assignment | TMF622 `add` item: `product.realizingResource` = a `ResourceRef` to an `idle`, `unlocked` number. A missing, unknown, locked or busy number rejects the order (`msisdnRequired`, `msisdnUnavailable`). Assigned when the order completes; one live product per number (constraint); no auto-pick. |
| Number change | TMF622 `modify` item with a new `product.realizingResource` at `requestedStartDate`; billing is unaffected and no interval is repriced. A combined number and offering change at a different date is refused; at the renewal boundary both apply in one transaction. |
| Release | At effective termination or cancellation the number returns to `idle` unless another accepted assignment starts then. Reuse observes `MSISDN_QUARANTINE_DAYS` (integer >= 0, **default 0**, the human's choice 2026-10-20), counted in `BILLING_TZ` dates from the stored release date. A setting change affects later checks only; an accepted future claim is grandfathered and the setting at acceptance is recorded in its audit. |
| IMSI | A second built-in specification `imsi` (see [decisions/prepaid.md](decisions/prepaid.md#imsi-issue-66)): `value` 14-15 digits, unique per specification (an IMSI and an MSISDN may share digits), optional `iccid` as a `StringCharacteristic` in `resourceCharacteristic`. `GET /resource?resourceSpecification.id=msisdn\|imsi` filters by kind. A releasing order locks the IMSI. |
| Lookup | `GET /resource?value=` finds the number; TMF637 `GET /product?realizingResource.id=` finds its product; product bodies return `realizingResource` with `name`. |

Assignment intervals are reserved when the order completes, also for future-dated orders,
and current usage is derived from the interval at the injected business date. A future
conflicting assignment makes a number unavailable for a new open-ended assignment even if it
is idle today. All affected numbers are locked in id order; a change ends the old interval
and starts the new one atomically. Quarantine starts at the effective release. Cancelling an
order reverses only what the cancellation workflow allows; if it would restore an assignment
that now conflicts with another accepted claim, it is a 409 and neither order changes.
Zero-length cancelled or same-day intervals stay as audit and claim no range. Locking an
assigned number withholds future assignments; it does not end the current product or bar
the line, and it does not revoke an accepted future order.

Creation body, `POST /tmf-api/resourceInventory/v5/resource`:

```json
{"@type": "LogicalResource", "name": "MSISDN 12025550123", "value": "12025550123",
 "administrativeState": "unlocked",
 "resourceSpecification": {"id": "msisdn", "@type": "ResourceSpecificationRef"}}
```

An order item's product carries
`"realizingResource": [{"id": "<number-uuid>", "@type": "ResourceRef", "@referredType": "LogicalResource"}]`.

## Prepaid balances: TMF654 mapping

| Resource | Operations and behavior |
|---|---|
| `bucket` | list, retrieve. One `monetary` bucket per prepaid billing account and currency, derived from the ledger: `remainingValue` (spendable), `reservedValue`, `status` `active` / `suspended` (account Suspended) / `expired` (account Closed), `partyAccount`, `product[]`, `logicalResource[]`, `isShared` when it funds more than one number. Ids are stable and persisted. |
| `accumulatedBalance` | list, retrieve. `totalBalance` is the sum of bucket `remainingValue` (L - H), not a second ledger. |
| `topupBalance` | All five. Needs `amount`, `usageType: monetary`, `bucket`, `partyAccount`; `logicalResource` may name the subscriber. Creates its TMF676 payment in the same transaction. |
| `reserveBalance` | All five. Server-created for each pending start and renewal (owned by the order or renewal); client-created as an operator hold (`validFor.endDateTime` optional, must be in the future; expired holds are released under the account lock before any later spending, and by the worker). |
| `adjustBalance` | All five. Operator credit (positive) or debit (negative), `oneTime`, signed nonzero. A debit cannot exceed spendable funds. |
| `transferBalance` | All five, with immutable monetary fields after acceptance. |
| `balanceActionHistory` | list, retrieve. Every top-up, reservation, adjustment and transfer with the same ids. |
| `/hub` + 12 event types | Create, cancel and failure names of the four actions through the existing outbox ([events](decisions/events.md)). `CreateEvent` records a newly accepted action (including failed); `FailureEvent` accompanies a failed result; `CancelEvent` an actual cancellation. Worker completion emits no invented state-change event; clients poll. Ordinary metadata changes emit nothing. |

Action lifecycle (`ActionStatusType`):

- A business refusal (insufficient funds, account not prepaid, number not live, wrong
  mode, inactive ownership, `sameBucket`) is stored `failed` with `reason`, a `FailureEvent`
  and a 201. Malformed or unknown references stay 400/422 with nothing stored.
- Top-up and adjustment execute synchronously and are `completed`. A hold is `created`
  while it holds funds, `completed` when consumed by its charge, `cancelled` when released.
  Amounts are explicit exact cents in the bucket currency; holds are non-negative (a
  zero-price product reservation may be zero).
- `PATCH status: cancelled` reverses a completed top-up or adjustment if the money is still
  unspent and unreserved (409 otherwise) and releases a held reservation. A reservation owned
  by an order or renewal is released only through that order. `DELETE` removes only `failed`
  actions.
- Client reservations cannot be consumed into a purchase (v4 has no commit-reservation
  operation): they are operator holds, released by cancel or expiry. Billing consumes only
  its own holds and marks them completed.

### Transfers

A transfer executes asynchronously so a fee can be posted by billing. POST validates both
account/number relationships under sorted account locks, reserves the sender's principal
plus the originator's fee, records `created`, queues one command and returns 201 with
`Location`; no principal moves yet. The fee is the exact tax-inclusive Money the operator
supplies, in the same currency, with no inferred tax; absent means zero, and a nonzero fee
with an omitted `costOwner` is persisted as `originator`. A nonzero fee requires a charge
and a settled `offCycle` bill; no fee means no placeholder bill.

For amount A and fee F: the originator pays A+F and the receiver gains A; if the receiver
pays, the sender pays A and the receiver gains A-F (F <= A is required, so the incoming A
funds the fee in the same transaction). All sender numbers resolve to one bucket and all
receiver references to the receiver bucket; a sender array is not several debits. Same
bucket, cross-currency, cross-mode or an unavailable number fails. Sender and receiver
claims are rechecked before posting; if no longer eligible the action fails and its holds
are released atomically with a `FailureEvent`. A receiving account must be Active or
Suspended. An accepted transfer is not cancellable, even while `created`; its command stays
recoverable and no timeout discards it. Principal, fee, source moves, charge, bill,
`completed` and the outbox commit together; `completed` is never returned before that.

## Mapping onto the existing APIs

| Spec element | Prepaid meaning |
|---|---|
| TMF666 `BillingAccount.ratingType` | `prepaid` or `postpaid`; settable on POST; PATCH only before financial or product history (409). |
| TMF622/637/678 `BillingAccountRef.ratingType` | Accepted when equal to the account's value; a mismatch is a 422. |
| TMF666 `creditLimit` | None or zero on a prepaid account; a positive value is a 422. |
| TMF666 `accountBalance[]` | One `currentBalance` per currency = the ledger balance L. Spendable and reserved amounts live on the TMF654 bucket, not in invented `balanceType`s. |
| TMF676 `payment` | Still the record of money received. A top-up creates its payment; a payment posted directly to a prepaid account (status `done`) creates exactly one linked completed top-up, without looping through the top-up API; non-`done` payments create none until done. |
| TMF622 order, insufficient funds | `rejected` with `productOrderErrorMessage` code `insufficientFunds` (required and available in `reason`). TMF637 POST product keeps its 409-with-reasons rule. |
| TMF622 order, funded immediate start | `inProgress` while the worker authorizes it, then `completed`. |
| TMF637 product | `pendingActive` until the worker posts the charge and grants the interval; `suspended` from the paid-through boundary when funds run out, back to `active` on the funded renewal. The cause (operator order or funding) is internal. |
| TMF678 ABR | Unchanged shape; `periodCoverage` is the funded interval (its end is the paid-through date), `isBilled: true` with the bill. Termination credits are ABRs. |
| TMF678 customerBill | `runType: offCycle`, `state: settled` from the reserved sources in the same transaction. Monthly `onCycle` invoicing stays postpaid-only. |
| TMF620 price | Unchanged; prepaid is the account's mode, not the price's. |

## Business rules

| Choice | Behavior |
|---|---|
| Mode | On the billing account (`ratingType`), one per account; a customer may own both kinds. Existing accounts stay postpaid with unchanged history. |
| Funding | TMF654 `topupBalance` (backed by a TMF676 payment). No overdraft, credit limit or grace period. |
| First period | A full interval from activation, also for monthly length 1. Postpaid keeps its calendar and proration rules. |
| Renewal | Automatic, a full interval at the price and tax applicable at its actual start, only when fully funded. |
| Insufficient initial funds | The order is `rejected` with `insufficientFunds`: no charge, hold, number claim or entitlement. |
| Insufficient renewal funds | The funded interval is not extended; the product is `suspended` (funding cause) at its paid-through boundary. `needs top-up` is a business outcome, not a broken run. |
| Top-up after expiry | Resumption is queued and a full interval starts on its authorization date; the unfunded gap is never billed. |
| Future starts | Numbers are claimed and the products stay `pendingActive`; funds are checked at the due date, not months ahead. A shortfall leaves the product pending with a funding reason. |
| Several products | Funded independently per account, in due business date then product UUID order; a shortfall for one does not block a cheaper later one. |
| Manual suspension | A separate persisted cause. It neither refunds nor ends a paid interval; at expiry it prevents renewal. A top-up never overrides an operator suspension, a Suspended account or a Closed one. Resuming reuses a live paid interval or queues authorization from today. |
| Termination | Effective on the requested business date, excluding that day. The unused covered dates of each affected charge are credited with the original net/tax rounding by difference, at most its uncredited amount; a same-day activation and termination refunds the whole period; an expired interval gets no credit. The billing owner creates the credit ABR, the `offCycle` credit document and the source credit; a previewed refund is not spendable before it commits. Owned holds and the number are released exactly once. |
| Offering change | At the next paid-through boundary, or on any date from today to it (mid-period: the unused dates are credited, the new first interval is held from current funds and bought from that date; [decision](decisions/prepaid.md#mid-period-offering-change-2026-11-04-issue-151)). After the boundary is `offeringChangeNotAtRenewal`; before the first grant, cancel and replace the pending start. Old and new offering and the boundary are persisted; the new one applies only when funded, otherwise service stays suspended with the change pending. No old-plan fallback is bought. A resumption after a gap uses the requested new offering. |
| Multi-item orders | All-or-nothing at acceptance and execution. The sum of new charges is reserved from current spendable funds (never from anticipated termination credits); one command applies the whole item tree atomically with one bill per purchased product interval. A future-order shortfall leaves the whole tree pending; later renewals are independent per product. Cancelling before the first grant cancels the command and releases holds and claims; after a grant, `cancelProductOrder` is a 409 (terminate instead). |
| Transfers, adjustments | Between prepaid accounts of one currency, limited to spendable funds including the fee; never across modes or currencies. Adjustments are goodwill credits and corrective debits. |
| Cash refund | A completed TMF676 refund limited to funds left after reservations; refunds never initiate an external transfer. Suspended accounts may receive top-ups and refunds but cannot start purchases, renewals, debits or outgoing transfers; refunds on Closed accounts stay permitted. |
| Closing | Refused (409) while the account has held money, unfinished commands or products not ended. Closed buckets stay readable as `expired`; closure never destroys credit. On Suspended accounts, termination and cancellation orders are permitted, new service is not. |
| Events | TMF639 resource events, TMF654 events, TMF637 state changes for `pendingActive`/`active`/`suspended`, TMF622 order state changes, all through the outbox. Status changes are stored and emitted when the worker observes them, with `eventTime` now; read-time expiry emits nothing on GET. |

## Funding and sources

Ledger balance L = completed payments + signed adjustments (including the compensating
entries of cancelled actions) + incoming transfers - outgoing transfers - tax-inclusive ABRs
- completed cash payouts; credit ABRs are already negative. Held reservations total H;
`reservedValue` = H and `remainingValue` = L - H. Bill allocations do not change L. Unused
funding sources are reconciled independently with unbilled obligations, payouts and
reservations; a mismatch is a technical failure.

Every consuming path takes the account lock and honors H: settlement, payout, debit,
transfer and prepaid posting. Reservations pin source amounts; completion converts holds to
allocations atomically with the charge, the settled bill and the funded interval. A
completed hold and its charge are never both subtracted. Positive adjustments and received
transfers introduce audited credit sources; debits and outgoing transfers consume specific
sources, like payouts. Cancelling a top-up or credit requires its source to be entirely
unused and unreserved; it appends an exact compensating entry without editing the immutable
original payment or inventing a payout, and cancelling a debit restores the exact consumed
sources through linked reversal entries. Reversal entries share the action's history id, are
not new user actions, and there is at most one per action. Received transfers become
spendable only on commit.

## Authorization, calendars and recovery

Entitlement is an explicit funded interval, never inferred from a balance or a stored status
alone. The effective product state uses the injected clock: an expired grant is
funding-suspended before the worker records it. `authorizedAt` and `paidThrough` are in the
internal view; service starts at the authorization commit, `paidThrough` is exclusive at
midnight of the end business date, and a late-in-the-day authorization buys that date in
full. The effective state is: terminated/cancelled first, then manually or account
suspended, then active only inside a funded interval, otherwise `pendingActive` before the
first grant or funding-suspended after one.

| Situation | Behavior |
|---|---|
| Immediate order, insufficient total | Rejected atomically with `insufficientFunds`; no number claim, hold, product or charge. |
| Immediate order, funded | Reserves the full quoted tax-inclusive total, claims numbers, creates `pendingActive` products and one durable command; order and items `inProgress`. The worker posts all items in one transaction, then completes them. |
| Command runs on a later business date | The accepted quote's amount, tax and cadence stay; a full interval starts on the actual authorization date. No service or charge for the gap; quote date and actual coverage are persisted separately. |
| Future order | Numbers claimed, products `pendingActive`, no early reservation. At or after the due date it authorizes from that day's funds and price; a shortfall keeps it pending with a funding reason. |
| Renewal on its due date | A full next interval from the due date, keeping the calendar anchor. |
| Renewal after its due date | No catch-up, even if money existed earlier: one full interval from today, anchor reset to today. |
| Top-up after expiry | Eligible work wakes on the next tick; the grant starts at the authorization date, never at the payment's supplied date. |
| Missing price, mismatched sources, SQL failure | A technical failure with durable retry diagnostics, never relabelled as a funding shortfall. |

Payments, holds and commands use server-received instants; a client `paymentDate` is
descriptive and grants no historical availability. The worker re-reads the clock after
taking account locks: a payment committed before the lock may fund the command, a later one
waits. One account's eligible product pass runs under its lock so ordering is deterministic.
Month and year boundaries derive from the original anchor with day clamping (not repeated
addition of clamped ends; February 29 is restored in leap years); days and weeks use
business dates across DST. No mid-period repricing exists. A worker outage cannot grant
unpaid service, and recovery never treats a later top-up as money available in the past.

## Worker, locks and retries

- **Runs.** Prepaid commands are identified separately from the unique daily postpaid run.
  Daily run ids, dates and history are unchanged; global date uniqueness became a partial
  unique daily-date constraint, plus a unique prepaid command identity. Commands never
  anchor the daily scheduler, postpaid replay, cycle dates, public billing-status or the
  sidebar run counts. The daily run excludes prepaid recurring charges and `onCycle`
  invoices; every ABR has a real run. An on-demand prepaid bill collects only existing
  unbilled ABRs, else it is rejected.
- **Ownership and order of work.** One worker holds the existing ownership advisory lock.
  On every wake-up (60 s) prepaid work runs first, up to 50 commands or product decisions,
  then one daily account step, the ordinary queues and event delivery. A long catch-up
  yields between accounts; a postpaid failure never blocks an independent prepaid command.
  Within prepaid work: expired operator holds, due terminations and cancellations, then
  accepted commands and renewals by due date and product id (a multi-product command uses
  its lowest product id, numberless transfers their action id, remaining ties the command
  id).
- **Lock order.** Ownership (worker only), idempotency identity, all billing accounts in
  UUID order, catalog rows under the existing shared-lock rules, affected products then
  numbers in UUID order, then source, hold and command rows. A path touching several
  accounts discovers and revalidates the set before taking child locks and never holds a
  product or number lock while acquiring another account. Read-only endpoints take no
  ownership lock.
- **Atomicity and retry.** Financial writes, the command result and events commit together.
  A technical failure rolls the money back and records error, attempt and backoff in a
  separate short transaction, keeping owned holds. Retry is after 1 min, 5 min, then hourly,
  plus a manual retry for an errored command that reuses the identity and cannot edit amounts.
  Funding shortfalls wait for a credit, account or operator change or a new price, and are
  scanned once per business date as a fallback. Failed daily accounts never drive prepaid
  recovery. Stranded reservations are reconciled through their command, never discarded.
- **Idempotency.** An optional `Idempotency-Key` (UUID) on TMF654 action POSTs, prepaid
  order POSTs and direct prepaid TMF676 payment POSTs is a documented transport extension.
  Without it each POST is a distinct action. It is scoped by operator, method and route; the
  normalized request hash (object key order and numeric spelling ignored, array order and
  absent/null significant) and the original status, body and `Location` are stored with the
  resource. The same key and body replays the original response; the same key with a
  different body is 409; malformed requests consume no key, stored failed business actions
  do; keys outlive a deleted failed action (tombstone); concurrent duplicates serialize on
  the key. The UI creates one key per deliberate submission and reuses it only for an
  operator's retry after an uncertain response; MCP exposes `idempotency_key`. Internal
  source/charge/grant uniqueness protects commands independently.
- **Internal operations** (in the internal contract): `GET /internal/v1/prepaid/command`
  (account filter, pagination), `GET …/command/{id}`, `POST …/command/{id}/retry` (202; 409
  unless `failedTechnical`; write scope in MCP). They return state, outcome, reason, due and
  attempt times and linked action, order, product and bill ids. The internal product and
  preview schemas carry `authorizedAt`, `paidThrough`, funding and manual causes, the pending
  offering and required/available/shortfall amounts; no public TMF654 shape gains them.

## Persistence

All migrations are additive. Existing rows stay postpaid and keep their payments, bills,
ABRs and immutable refunds; legacy source totals were reconciled before the new constraints.
Downgrade refuses number inventory or prepaid history the previous schema cannot represent
and never drops it; the number and funding milestones have separate barriers.

| Storage | Identity and protection |
|---|---|
| `subscriber_number` | UUID, unique validated value, administrative state, descriptive JSON. |
| `number_assignment` | Number, product, assigning and ending item, half-open business-date range, release date, quarantine at acceptance, audit sequence; GiST exclusions prevent overlapping live claims. |
| `billing_account.rating_type` | Checked prepaid/postpaid, postpaid default; mode change only before any product, financial, action or command history; prepaid credit limit null or zero. |
| `prepaid_bucket` | Stable account and currency identity; no mutable balance. |
| `balance_action` | Kind, status, checked request, Decimal amounts, references, server times, reason, operator, command link and a unique reversal marker; history is its projection. |
| funding source, consumption, hold | The payment and credit-note sources plus adjustment, transfer and reversal kinds; one owner and currency, unique command consumption, deferred combined bounds over allocations, payouts, debits, transfers and holds. |
| `funded_interval` | Product, authorized instant, half-open coverage dates, anchor and index, quoted price and tax, command, run and ABR links; no overlapping grants per product. |
| `prepaid_command` | Kind, owner, due date, immutable quote, state `pending`/`completed`/`failedTechnical`, attempts, next retry, outcome, links. Shortfalls are outcomes, not broken runs. |
| pending product change, suspension cause | Owning order and item, effective boundary, requested offering, and the operator cause independent of funding expiry. |
| idempotency journal | Unique operator, method, route and key; request hash; original response; resource or tombstone. |

## API examples and UI flow

`POST /tmf-api/prepayBalanceManagement/v4/topupBalance` (with an `Idempotency-Key`):

```json
{"@type": "TopupBalance", "amount": {"amount": 50.00, "units": "USD"},
 "usageType": "monetary", "bucket": {"id": "<bucket-id>"},
 "partyAccount": {"id": "<account-id>", "@referredType": "BillingAccount"}}
```

The 201 returns the action with status `completed`, `requestedDate` and `confirmationDate`;
the same transaction records one `done` payment.

`POST …/transferBalance`:

```json
{"@type": "TransferBalance", "reason": "Customer requested credit transfer",
 "channel": {"id": "admin-ui", "name": "Admin UI"},
 "logicalResource": [{"id": "<sender-number-id>"}],
 "receiverLogicalResource": {"id": "<receiver-number-id>"},
 "amount": {"amount": 10.00, "units": "USD"}, "usageType": "monetary",
 "bucket": {"id": "<sender-bucket-id>"}, "receiverBucket": {"id": "<receiver-bucket-id>"},
 "receiverBucketUsageType": "monetary",
 "transferCost": {"value": 1.00, "unit": "USD"}, "costOwner": "originator"}
```

It returns `created`. With sender L=50 and no holds the sender bucket shows remainingValue
39 and reservedValue 11 and the receiver is unchanged; on completion the sender has L=39/H=0,
the receiver gains 10 and the fee bill for 1 is settled.

A prepaid purchase of 30 including tax with L=50/H=0 returns the order `inProgress` and the
product `pendingActive`, with remainingValue 20 and reservedValue 30; posting changes L to
20 and H to 0 and grants the interval. L=29.99 rejects without a hold, also when the net
catalog price is lower but tax makes the total 30.

UI: `/numbers` is the inventory; mode and funding live on the subscriber pages. Number search
keeps the exact value; the picker checks the proposed start date, shows future reservations
and quarantine, and revalidates on submit. New subscriber keeps its party, customer and
account steps and adds the offering and number order after the account exists; a prepaid
account can be created empty and topped up before Add product, and each failed step lists
what already exists. The mode is chosen at creation and shown and filterable. A prepaid card
shows ledger balance, available, reserved, per-product paid-through and authorization, and the
shortfall cause, from the internal product and preview views (also read-scope MCP tools),
visibly apart from the account balance. Controls: top-up, adjustment, transfer by number,
operator hold and release; the transfer confirmation shows both numbers and accounts, the
principal, the fee owner, the net recipient credit and that it is final. Pending orders and
transfers are polled every 5 s for at most 3 min, then an explicit refresh is offered;
needs-top-up is shown apart from technical failure; the retry button applies only to
technical commands. An uncertain POST offers a same-key retry, never a fresh duplicate.
Request bodies, headers and decimal encoding are pinned in frontend and backend tests, in
both configured time zones. MCP keeps read and write scopes and decimal-string amounts.

## Tests

The proofs are `tests/prepaid/`, `tests/funding/`, `tests/resources/test_number_*.py`,
`tests/internal/test_ui_requests.py` and the frontend suites: exact funds and one cent short with tax,
zero prices, every cadence, month ends, leap years, DST and midnight boundaries; late worker
recovery buying one interval from today; duplicate and concurrent submissions with the same
or different keys; reserved sources against settlement, payouts, debits and transfers; final
transfers with both fee owners; scheduled number claims, requirement changes after order
history and quarantine changes; mixed prepaid and postpaid reconciliation; schema validation
against the vendored contracts (except the one history exception); upgrade of populated
history as the database owner and refusal of lossy downgrades.
