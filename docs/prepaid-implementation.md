# Prepaid implementation plan

Status: **design complete**, 2026-10-20. The human authorized remaining choices
according to TMF and authorized committing/pushing design only. This document and
[the scope plan](prepaid-plan.md) are the development hand-off; this document wins
where it corrects the original proposal. Development begins with contract vendoring
and executable fixtures in N1; runtime slices follow. Numbers ship independently first.

N1 development: the two pinned files are now vendored unchanged, with executable
contract fixtures, provenance checks and 36 planned/absent endpoint records. No
prepaid runtime exists yet. N2 adds number persistence, N3 adds the inventory API,
hubs, events and MCP tools; order integration follows in N4. See the
[implementation record](decisions/prepaid.md#n1-pinned-contracts-and-executable-fixtures-2026-10-20).

## Contract findings

Inspected official [TMF639](https://github.com/tmforum-apis/TMF639_ResourceInventory/blob/2693a854940de3c6e15b1aade24f981d94b75d3a/TMF639-Resource_Inventory_Management-v5.0.0.oas.yaml)
and [TMF654](https://github.com/tmforum-apis/TMF654_PrepayBalanceManagement/blob/562e655d14d796b8380b4e244a5b601f69d60b64/TMF654-PrepayBalance-v4.0.0.swagger.json)
schemas at these pinned revisions. N1 vendors these exact files, unmodified:

| Contract | Git blob SHA | SHA256 |
|---|---|---|
| TMF639 v5.0.0 | `2c34d9772bb937e4aebb59ae71d1a6825df39737` | `60f1bd928326d8cf50bbfe6f6fada952005588c2598f63d72ae8c07d2048b881` |
| TMF654 v4.0.0 | `60378955ca383e0f2e6427d57d9d9d5ed1ddb349` | `f397aea173cffeedb0bad4d7dd89ea3e469b881ed6e350828325c3ffa2751920` |

- TMF639's server variable defaults to `https://serverRoot/resourceInventory/v5`.
  Use `/tmf-api/resourceInventory/v5`; the base-path probe must resolve the variable
  default instead of treating `{apiRoot}` as an empty path.
- Assigned numbers are `busy`: the spec defines `active` as having spare capacity
  for additional users. `allocationStatus` is a free string, allowing `quarantined`.
- TMF639 has five resource operations, POST/GET/DELETE hub operations and four
  listeners. Unsupported resource subclasses need explicit gap reasons.
- TMF654's base is `/tmf-api/prepayBalanceManagement/v4`; it has 28 operations
  excluding its twelve listeners: six reads, twenty action operations and two hub
  operations.
- Action amounts and bucket figures use Quantity (`amount`, `units`); transfer cost
  uses Money (`value`, `unit`). Read/write both as exact Decimal JSON numbers.
- Transfer POST requires reason, receiverLogicalResource, channel, logicalResource,
  amount, usageType, bucket, receiverBucket and receiverBucketUsageType. Sender
  logicalResource is a nonempty array; receiverLogicalResource is one object.
- Adjustment POST requires amount, usageType and bucket; its create schema has no
  partyAccount. Reservation POST requires usageType, bucket and partyAccount;
  amount is optional. This release explicitly requires an amount (422 if absent):
  there is no usage authorization request from which to infer a hold. Record this
  restriction in the gap ledger; do not silently reserve all available credit.
- Top-up POST requires amount, usageType, bucket and partyAccount. paymentMethod is
  optional. Its omission generates a payment labeled `Unspecified recorded receipt`,
  type `PaymentMethodRef`, with no external method id. It does not imply cash or
  collected money. A provided PaymentMethodRef requires id; preserve its metadata
  and map its name/type to the generated payment without inventing a method catalog.

The official [TMF654 release directory](https://www.tmforum.org/open-digital-architecture/open-apis/prepay-balance-management-api-TMF654/v4.0)
lists v4.0 as stable and v5.0 as a preview. Select the stable v4.0 contract mirrored
in the official GitHub repository. This corrects the proposal's earlier claim that
no v5 was published. Do not mix preview/4.1 model fields into v4 requests.

TMF defines shapes and prepaid-before-service semantics; the retry, accounting,
calendar and recovery choices below are local policies where these contracts do
not specify an algorithm. They are not claims of TMF conformance certification.

### Contract corrections and explicit deviations

TransferBalance_Update has no status or description, and requires reason, channel,
logicalResource and receiverLogicalResource. Therefore **transfers are final** once
accepted. Corrections are new opposite transfer requests, subject to current funds
and the same checks; they do not erase the original or automatically refund its fee.
Do not add an undocumented cancellation attribute or endpoint. Transfer PATCH takes
the four required fields, permits reason/channel/requestor metadata changes, and
requires monetary/resource fields to equal their stored values (409 otherwise).
TransferBalanceCancelEvent is subscribable but never emitted in this release, as
other unreachable spec events already are. DELETE still removes failed actions only.

Other action PATCH requests obey their own schema: top-up and adjustment require
status; reservation requires status and reason. Repeating current status permits
descriptive edits; cancelled requests reverse a reversible credit/debit or release a
client hold. Amount, currency, ownership and source references cannot be rewritten
after acceptance (409). Dates/status managed by the server accept equal values only.
v4 PATCH consumes application/json as declared; do not promise v5-only content types.

BalanceActionHistory incorrectly requires receiverLogicalResource even for actions
whose schemas have none. Return a truthful projection with the action's original id,
history href, status and applicable fields; no fictitious number, especially for
numberless accounts. The sole response-schema exception removes that required key
for non-transfer entries. Validate transfers against the unchanged schema and apply
only this conditional exception for others, including event/test fixtures. Record
the exact exception in the gap ledger; raw-schema validation is not claimed for
these entries. Required status is never relaxed. Include reservations in history
(the union's description omits them but its fields can represent them).

Record all accepted restrictions in tests/tmf-gaps.txt during implementation: the
proposal's excluded usage/automation/voucher cases, required reservation amount,
explicit monetary Quantity amount/units, immutable executed-action financial fields,
number-only inventory subclasses/states and the history exception. Keep endpoint
coverage, request/response gaps and unreachable events separate. Listeners are
subscriber endpoints; the BSS sends events, it does not add listener routes.

### Action field defaults

For top-up/adjustment/transfer, validFor is descriptive audit metadata, not delayed
execution or expiry of monetary credit. Preserve it as sent; recurring/automatic
processing stays excluded. For reserveBalance, validFor.endDateTime expires the hold;
start must be absent or no later than now because future operator holds are outside
this release (422 otherwise, recorded restriction). Amount and units must be explicit;
do not use Quantity's schema default amount=1 for a missing monetary amount. An
omitted adjustType defaults to oneTime; omitted isAutoTopup defaults to false. Reject
automatic period/count/voucher fields rather than treating them as descriptive.

External method/channel/requestor metadata is stored as sent and schema checked, not
resolved against unvendored catalogs. Resolved bucket/account/product/number references
must agree; optional top-up logicalResource/product must belong to the bucket account
and identify its current products/numbers. Without a supplied payment-method name,
use its id as the generated payment's display label, preserving the submitted reference
in the action. Never imply that a referenced method collected money. Top-up records
a receipt asserted by an authorized operator.

## Subscriber-number slices

| Slice | Reviewable result |
|---|---|
| N1: contracts | Vendor both unmodified files with pinned commits, blob SHAs and SHA256 sums. Extend provenance, schema helpers and operation/gap accounting for Swagger 2 TMF654 and TMF639 server variables. Preserve the existing gap lock and explicitly track unavailable operations. |
| N2: persistence | Next unused migration: unique normalized numbers and effective-dated assignment history linked to products and order items, plus release business dates. Database constraints prevent overlapping assignments of a number and overlapping numbers for one product. Downgrade refuses retained inventory/history. Follow the schema design below; propose the migration before applying under the normal project workflow. |
| N3: inventory | TMF639 CRUD, fields, paging, filters, PATCH formats and nested validation. Immutable number value and built-in specification; editable descriptions and lock/unlock. Delete only never-assigned numbers. Hubs, outbox events, MCP tools and contract tests. |
| N4: orders | Built-in msisdn requirement, add/modify/release/cancel through effective-dated assignments and product lookup by realizingResource.id. Preserve existing unchecked documents on products whose specifications do not require numbers. |
| N5: UI | Number inventory, create/search/lock, number selection in New subscriber, details and number changes through orders; pinned request tests and browser coverage. |
| N6: release | Negative proofs, three backend passes, lint, frontend/browser checks, contract validation and progress generation. Review diff before separate commit and deployment approval. |

Read catalog, events, frontend and MCP decision records before their slices. Supply
protected documentation-generator/harness edits as reviewable patches when approval
is needed; never bypass guards.

Future-dated postpaid orders already complete synchronously. Reserve the assignment
interval when their order completes, before service starts. Derive current usage from
the interval at the injected business date. A future conflicting assignment makes a
number unavailable for a new open-ended assignment even if currently idle. A partial
unique index over today's status cannot protect future assignments.

Lock all affected numbers in stable id order before checking or changing assignments.
A change ends the old interval and starts the new one atomically. Quarantine begins
at effective release, not at earlier order submission. Cancellation reverses only
transitions allowed by the existing order cancellation workflow, retaining audit
history. Cancellation that would restore an assignment now conflicting with another
accepted claim is a 409 with neither order changed. Test future starts, modifications,
cancellation and both quarantine settings.

Number rules: E.164 digits, no plus, 8..15 characters, first digit 1..9, uniqueness
on the entire value; never silently strip formatting. Persist value as text.
The built-in `msisdn` specification is a fixed reference, not a fake TMF634 URL;
omit href because no resource catalog is implemented. A product specification may
add/remove this requirement only while no offering using it has any order history;
otherwise 409, to avoid changing live product requirements by editing a document.
Resolve the requirement at ordering and snapshot it on the product. Unknown resource
specification references remain documents as before unless their id is `msisdn`.

Number availability is evaluated for the requested interval, including future
assignments and releases. With quarantine N, earliest reuse is release date + N;
N=0 permits same-date reuse. A setting change affects later checks, including already
scheduled assignments. An increase cannot revoke an accepted assignment: the accepted
order's eligibility is grandfathered, with the quarantine setting recorded in its
assignment audit. New assignments use the current setting. The unique/exclusion
constraint protects actual assignment overlap; quarantine is checked under the same
number lock. Zero-length cancelled/same-day intervals leave audit history but claim
no range. Future claims expose allocationStatus `reserved`, current claims `allocated`,
otherwise `quarantined` or `available`; usageState describes current service use.

Locking an assigned number withholds future assignments; it does not terminate its
current product or simulate network barring. Current use remains busy. A lock placed
after a future postpaid assignment was accepted does not revoke that order. A prepaid
future start must still pass account/funding checks at execution; its owned number
claim is retained while pending. Termination/cancellation explicitly releases it.

Target number creation body, POST `/tmf-api/resourceInventory/v5/resource`:

```json
{
  "@type": "LogicalResource",
  "name": "MSISDN 12025550123",
  "value": "12025550123",
  "administrativeState": "unlocked",
  "resourceSpecification": {"id": "msisdn", "@type": "ResourceSpecificationRef"}
}
```

An add/modify order item's product includes:

```json
{
  "@type": "ProductRefOrValue",
  "realizingResource": [
    {"id": "<number-uuid>", "@type": "ResourceRef", "@referredType": "LogicalResource"}
  ]
}
```

N1 must turn these target examples into schema-validated fixtures before runtime work.

## Funding proofs before exposure

Keep prepaid account creation unavailable until shared consumption and recovery proofs
pass. Implement schema/core and worker together as the proposal requires.

Ledger balance L = completed payments + signed adjustments + incoming transfers -
outgoing transfers - tax-inclusive ABRs - completed cash payouts. Credit ABRs are
already negative. Held reservations total H. reservedValue = H; remainingValue = L - H.
Bill allocations do not change L. Independently reconcile unused funding sources with
unbilled obligations, payouts and reservations; mismatches are technical failures.
Signed adjustments in this equation include compensating action-reversal entries;
do not omit a cancelled top-up's debit while its original payment remains done.

Every consuming path acquires the account lock and honors H: explicit/automatic
settlement, payout, debit, transfer and prepaid posting. Reservations pin source
amounts. Completion converts holds to allocations atomically with charge, settled
bill and funded interval. Never subtract both a completed hold and its charge.

Keep source accounting unified: positive adjustments and received transfers introduce
audited credit sources; debits and outgoing transfers consume specific sources, like
payouts. Cancelling a top-up/positive adjustment requires its original source to be
entirely unused and unreserved, even when other funds could replace it. Cancellation
appends an exact compensating ledger entry and source consumption, without editing
the immutable original payment or creating a fictitious cash payout. Cancellation
of a debit restores the exact consumed sources through linked reversal entries.
Reversal entries share the original action history id; they are not new user actions.
At most one reversal per action. Received transfers become spendable only on commit.

TMF666 currentBalance is L; accumulatedBalance.totalBalance is the sum of bucket
remainingValue (L - H), not a second ledger balance. One bucket/accumulation per account
and currency has a stable persisted id. Closed buckets stay readable with expired
status; closure does not destroy credit. Refuse closing a prepaid account with held
money, unfinished commands or products not yet ended (409); terminate them by order
first. On Suspended prepaid accounts permit termination/cancellation cleanup orders
but refuse new service/purchases; this does not authorize funds to be spent.
Suspended accounts may receive top-ups and refunds but cannot initiate purchases,
renewals, debits or outgoing transfers. Refunds on Closed accounts remain permitted.
Receiving a transfer requires an Active or Suspended prepaid account; Closed fails.

## Authorization, calendars and recovery

Entitlement is an explicit funded interval, never inferred from a balance or a
product's stored status alone. Return effective product state using the injected
clock: an expired grant is funding-suspended even before the worker records the
transition. No provisioning integration is promised. Expose authorizedAt and
paidThrough in the internal view; actual service starts at authorization commit,
with paidThrough exclusive at midnight of the end business date. Financial coverage
uses business dates; a late-in-the-day authorization buys that date in full, as
activation already does. Do not backdate actual entitlement to midnight.

| Situation | Chosen behavior |
|---|---|
| Initial immediate order, insufficient total | Reject atomically with insufficientFunds; no number claim, hold, product or charge. |
| Initial immediate order, funded | Reserve the full quoted tax-inclusive total, claim numbers, create pendingActive products and durable command; order/items inProgress. Worker posts all items in one transaction, then completes them. |
| Immediate command runs on a later business date | Keep the accepted quote's amount/tax/cadence; start a full interval from the actual authorization date, recomputing its end. No service or charge for the waiting gap. Persist quote date and actual coverage separately. |
| Future order | Claim numbers and leave products pendingActive, order/items inProgress; no early fund reservation. At/after the due date authorize from today's funds and price. Shortfall keeps it pending, with a funding reason. |
| Renewal processed on its due business date | Full next interval from the due date, at applicable price/tax. Preserve its original calendar anchor. |
| Renewal processed after its due business date | Never catch up missed service periods, even if money existed earlier. Authorize one full interval starting today and reset its calendar anchor to today. No debt or service in the gap. |
| Top-up after expiry | Wake eligible work on the next normal tick; grant from authorization date, never payment's supplied historical date. No automatic override of suspension or account state. |
| Several due products | Within the account, due business date then product UUID; oldest due first, persist outcome sequence. Insufficient funds for one do not block a cheaper later product. |
| Missing price, mismatched sources or SQL failure | Technical failure and durable retry diagnostics; never relabel as a funding shortfall. |

Payments, holds and commands use server received/committed instants. A client-supplied
TMF676 paymentDate is descriptive and cannot grant historical funding availability.
A worker reads the clock again after taking account locks; a payment committed before
that lock may fund the command, one committed later waits for a later attempt. Keep
one account's eligible product pass under its lock to make ordering deterministic.

Month/year boundaries derive from the original grant sequence anchor with day clamping,
not repeated addition of clamped ends; restore February 29 in leap years. Days/weeks
use business dates across DST. Monthly length 1 prepaid also buys a full activation
interval; only postpaid retains calendar-cycle proration. Renewal uses the current
price and tax at its actual new interval start, not a historical outage boundary.
No mid-period repricing or automatic alteration-price arithmetic is introduced.

Manual suspension is a separate persisted cause. It ends neither an existing paid
interval nor its charge; at expiry it prevents renewal. Resuming by order reuses any
still-live paid interval or queues authorization from today if expired. Account
Suspended/Closed also prevents authorization. Top-up changes none of these causes.
An effective product is terminated/cancelled first, then manually/account suspended,
then active only inside a funded interval; otherwise pendingActive before its first
grant or funding-suspended after one. Status changes are stored/emitted when observed
by the worker, with eventTime now; read-time expiry does not emit on GET.

Termination is effective on the requested business date and excludes that day. Refund
the unused covered dates of each affected original charge, with original net/tax
rounding by difference, at most its uncredited amount. A same-date activation and
termination refunds the full period. Already expired intervals get no termination
credit. The billing owner creates the credit ABR, offCycle credit document and source
credit; until it commits, a previewed refund is not spendable. Release future owned
holds exactly once and release the number at effective termination. APIs may stop
entitlement immediately through the order, but never mint the credit ABR themselves.

An offering-change order is scheduled only for the next paid-through boundary.
Reject other effective dates (409); before the first grant, cancel and replace the
pending start instead. Persist the old/new offering and boundary; apply the new one
only when successfully funded. If it cannot fund, leave service suspended with the
change pending. No old-plan fallback is silently purchased. Resumption after a gap
uses the requested new offering. Number-only changes do not reprice an interval.
Reject a combined number/offering change at a different effective date; permit both
at the renewal boundary in the same transaction.

Prepaid multi-item orders stay all-or-nothing at acceptance and execution. Reserve
the sum of new charges using current spendable funds, never anticipated termination
credits. One order command applies its complete item tree atomically, with one bill
per purchased product interval; a technical error rolls back the whole command.
Future-order shortfalls leave the whole tree pending. Later automatic renewals are
independent per product. Cancellation before a first grant cancels the command,
releases owned holds/claims and marks items/products cancelled. After a grant,
cancelProductOrder follows the existing no-effective-change rule (409); use a
termination order for a refund. Do not cancel an owned reserveBalance directly.

## Action execution and transfers

Top-ups, adjustments and operator reservations execute synchronously: valid business
refusals are stored failed/201 with reason; malformed/unknown references are 400/422
with no action. A valid reference to the wrong mode, inactive ownership, shortfall or
same bucket is a business failure. Top-up/adjustment complete; a hold stays created
until release or consumption. Amounts are explicit exact cents in the bucket currency;
top-ups/transfers positive, adjustments signed nonzero, holds nonnegative. Zero-price
product reservations may be zero. An operator hold with omitted end has no expiry;
an end must be in the future. Expiry releases it before any later spending, including
API spending before the worker's cleanup tick, under the account lock. Worker-owned
holds have no independent expiry and can only be resolved by their command.

Transfers execute asynchronously so a fee can be posted by billing. POST validates
both account/number relationships under sorted account locks, reserves the sender's
principal plus originator fee (or sender principal only), records created and queues
one command; 201 with Location. No principal moves yet. Fee absent means zero;
nonzero fee with omitted costOwner defaults to originator, persisted and returned.
The fee is the exact tax-inclusive Money supplied by the operator, same currency,
with no separate inferred tax (tax zero for this first release). It is not a TMF620
recurring price. Nonzero cost requires a charge and settled offCycle bill; no fee
means no placeholder bill. This corrects the original synchronous-transfer proposal.

For amount A and fee F: originator pays A+F and receiver gains A; if receiver pays,
sender pays A and receiver gains A-F. Require F <= A for receiver-paid fees; the
incoming A funds its fee in the same transaction, with no temporary spendable credit.
Resolve all sender numbers to one bucket, all receiver references to the receiver
bucket; do not interpret a sender array as multiple independent debits. Same bucket,
cross-currency/mode or unavailable number fails. Fee/principal effects, source moves,
reservations, charge/bill, action completed and outbox commit atomically.
Sender and receiver claims are rechecked before posting; if no longer eligible,
fail the action and release its holds atomically, with a FailureEvent.

An accepted transfer is not cancellable, even while created. Its command remains
recoverable after death; no timeout silently discards it. A new reverse transfer can
correct a completed transfer only with current funds. Client-created reservations
cannot be consumed into a purchase via a nonexistent v4 commit-reservation API: they
are operator holds, released by cancel/expiry. Billing consumes only its own holds
internally and marks them completed. This is a local lifecycle mapping, not a claim
that v4 defines a separate reservation commit operation.

CreateEvent records a newly accepted action's initial state (including failed).
FailureEvent accompanies the failed result; CancelEvent accompanies an actual
supported cancellation. Worker completion has no invented TMF654 state-change event;
clients poll action/history. Register all twelve declared event names, including
the unreachable transfer cancel name. Use existing transactional outbox delivery
and callback policy. Ordinary metadata changes emit no invented event type.

## Retry identities and worker ownership

Use optional `Idempotency-Key` (UUID) on TMF654 action POSTs, prepaid-order POSTs and
direct prepaid TMF676 payment POSTs. This is a documented transport extension, not
an invented v4 body id. Without it each POST is a distinct action, as normal TMF
creation semantics allow; no duplicate-submission guarantee is claimed without a key.
Scope by authenticated operator, method and route, and store the normalized request
hash plus original status/body/Location atomically with its resource. Object key order
and numeric spelling do not affect the hash; array order and absent/null do.
Same key/body replays the original response; current state is obtained by Location.
Same key/different body is 409. Malformed requests do not consume keys; stored failed
business actions do. Retain keys for the lifetime of the audit, even if a failed action
is deleted (retain its tombstone/response). Concurrent duplicates serialize on the
key and return one action. A request that rolls back leaves neither key nor action.

The UI creates one key per deliberate submission and reuses it only for an operator's
retry after an uncertain response; no automatic write retry. MCP exposes an optional
idempotency_key argument mapped to the header; it cannot be silently regenerated per
retry. Internal source/charge/grant uniqueness protects commands independently.

Separate daily and prepaid-command billing runs. Keep existing daily IDs/dates;
replace only the global date uniqueness with a partial unique daily-date constraint,
and add unique prepaid command identity. Commands never anchor the daily scheduler,
postpaid replay, cycle dates, public billing-status or sidebar run counts. Daily
financial processing excludes prepaid recurring charges/onCycle invoices; generic
settlement still honors reservations. On-demand prepaid bills collect only existing
unbilled ABRs; otherwise reject, never create a second charge/document.

One worker retains the existing ownership advisory lock. Prepaid work is processed
before the daily backlog on every wake-up, up to 50 commands/product decisions;
then one daily account step, ordinary queues and event delivery get their normal
turn. The daily scheduler's dates/cutoffs/retry rules remain unchanged. A long catch-up
must yield between accounts so prepaid work is revisited; a postpaid run failure
does not block an independent prepaid command. Use existing stop/heartbeat checks.
Within prepaid work process expired operator holds, due termination/cancellation,
then accepted commands and renewals in due-date/product-id order. Transactional
number-only transitions may run without a financial run; every ABR has a real run.
For a multi-product command use its lowest product id as its tie-breaker; numberless
transfers use their action id. Break any remaining ties by immutable command id.

Lock order: ownership (worker only), idempotency identity, all billing accounts in
UUID order, catalog rows under existing shared-lock rules, affected products then
numbers in UUID order, source/hold/command rows. All paths touching more than one
account discover/revalidate the account set before taking child locks. Do not hold
a product/number lock while acquiring another account. Adapt existing writers to
this common order and test payments, refunds, transfers in both directions, orders,
price changes and renewals for deadlocks. Read-only endpoints need no ownership lock.

Financial writes, command result and events commit together. On process death before
commit none took effect; after commit, retry returns the stored result. Technical
failure rolls back money changes, records error/attempt/backoff in a separate short
transaction, and retains owned holds. Retry after 1 min, 5 min, then hourly, with a
manual internal retry operation for an errored command; it reuses the same identity
and cannot edit amounts. Funding shortfalls wait for a credit/account/operator change
or a new applicable price and are scanned once per business date as a fallback.
No retry of a failed daily account is used to drive prepaid command recovery.

Internal operations to add to the vendored internal contract: GET
`/internal/v1/prepaid/command` (account filter, pagination), GET
`/internal/v1/prepaid/command/{id}`, POST
`/internal/v1/prepaid/command/{id}/retry` (no body, 202 with the same queued command;
409 unless failedTechnical). Return state, outcome, reason, due/attempt times and
linked action/order/product/bill ids. Existing authenticated user/service rules apply;
retry is write-scope in MCP. Extend internal product/preview schemas for authorizedAt,
paidThrough, funding/manual causes, pending offering and required/available/shortfall
amounts. No public TMF654 shape gains these internal fields.

## Persistence and migration design

Use additive migrations, choosing revision numbers at development time:

| Storage | Required identity and protection |
|---|---|
| subscriber_number | UUID, unique validated value, admin state, descriptive JSON, metadata/timestamps; fixed msisdn reference. |
| number_assignment | Number, product, assigning/ending item, half-open business-date range, release date, quarantine-at-acceptance and audit sequence. GiST exclusions prevent simultaneous overlapping live claims for number/product; cancelled versions remain separate audit records outside active exclusions. |
| billing_account.rating_type | Checked prepaid/postpaid with postpaid default. Mode change only before all product/financial/action/command history; prepaid credit limit null/zero. |
| prepaid_bucket | Stable account/currency identity, no mutable balance. Composite FKs enforce currency/account ownership. |
| balance_action | Kind, ActionStatusType, original checked request, Decimal amounts/currency, references, server times, reason, operator, command link and unique reversal marker; history is its projection. |
| funding_source / consumption / hold | Reuse existing payment/credit-note sources and bounds; add adjustment/transfer/reversal source kinds. Amounts nonnegative, one owner/currency, unique command consumption; deferred combined bounds cover bill allocations, payouts, debits, transfers and held funds. Source rows cannot be silently deleted/reassigned. |
| funded_interval | Product, actual authorized instant, half-open coverage dates, original anchor/index, quoted price/tax and command/run/ABR links. No overlapping committed grants per product; unique command/product/interval identity. |
| prepaid_command | Kind/owner, due date, immutable quote/request, state pending/completed/failedTechnical, attempts, next retry, outcome and links. Internal states are not extra TMF enums. Business shortfalls are outcomes, not broken runs. |
| pending product change / suspension cause | Owning order/item, effective boundary, requested offering and operator cause independent of funding expiry; no rewriting already paid coverage. |
| idempotency journal | Unique operator/method/route/key, request hash, original response, resource/tombstone and commit audit. |

Reconcile legacy source totals before enabling the new constraints. Existing rows stay
postpaid and retain all payments, bills, ABRs and immutable refunds unchanged. Upgrade
tests must include populated financial history and the actual database-owner role.
No migration disables refund immutability to make this feature work. Downgrade refuses
number inventory/assignment or prepaid/action/source/command history that the previous
schema cannot represent; it never drops that history automatically. Number milestone
rollback and funding milestone rollback have separate barriers. Keep ratingType
prepaid rejected until all money paths and recovery acceptance tests pass.

## API examples and UI flow

All ids below stand for real resource ids. Convert these examples to contract tests
during N1, including required fields and the explicitly documented history exception.

POST `/tmf-api/prepayBalanceManagement/v4/topupBalance` with an Idempotency-Key:

```json
{
  "@type": "TopupBalance",
  "amount": {"amount": 50.00, "units": "USD"},
  "usageType": "monetary",
  "bucket": {"id": "<bucket-id>"},
  "partyAccount": {"id": "<account-id>", "@referredType": "BillingAccount"}
}
```

201 returns this action plus server id/href/status completed, requestedDate and
confirmationDate. The same transaction records one done TMF676 payment. A direct
done payment on prepaid creates exactly one linked completed top-up without looping
back through the top-up API; non-done payments create no funds/top-up until done.

POST `/tmf-api/prepayBalanceManagement/v4/transferBalance`:

```json
{
  "@type": "TransferBalance",
  "reason": "Customer requested credit transfer",
  "channel": {"id": "admin-ui", "name": "Admin UI"},
  "logicalResource": [{"id": "<sender-number-id>"}],
  "receiverLogicalResource": {"id": "<receiver-number-id>"},
  "amount": {"amount": 10.00, "units": "USD"},
  "usageType": "monetary",
  "bucket": {"id": "<sender-bucket-id>"},
  "receiverBucket": {"id": "<receiver-bucket-id>"},
  "receiverBucketUsageType": "monetary",
  "transferCost": {"value": 1.00, "unit": "USD"},
  "costOwner": "originator"
}
```

201 initially returns status created. With sender L=50 and no previous holds, the
sender bucket reports remainingValue 39, reservedValue 11; receiver is unchanged.
On completion sender L=39/H=0, receiver gains 10 and the fee bill for 1 is settled.
Never return completed before its source moves and bill commit.

A prepaid purchase of 30 including tax with L=50/H=0 returns order inProgress and
product pendingActive; its bucket is remainingValue 20/reservedValue 30. Successful
posting changes L to 20/H to 0 and grants the interval. L=29.99 rejects without a
hold, including when the catalog net price is lower but tax makes the total 30.

UI routes: `/numbers` for inventory, existing subscriber/account pages for mode and
funding. Number search preserves the exact value; the picker checks the proposed
start date, shows future reservations/quarantine and revalidates on submit. New
subscriber keeps its existing party/customer/account steps and adds the chosen
offering/number order after account creation; a prepaid account can be created empty
and topped up before Add product. Each failed step lists what already exists.

Choose prepaid/postpaid at creation; show/filter mode. On a prepaid card show ledger
balance, available, reserved, product paid-through/authorization and shortfall cause.
Do not invent extra TMF654 bucket properties for per-product dates: use documented
internal product/preview views, also exposed through read-scope MCP tools. Keep the
account balance and bucket figures visibly distinct. Add Record top-up, adjustment,
transfer by number and operator hold/release controls; transfer confirmation shows
both numbers/accounts, principal, fee owner, net recipient credit and finality.

Poll pending orders/transfers every five seconds for at most three minutes, then
offer explicit refresh. Show needs-top-up separately from technical failure. The
retry button applies only to technical commands and reuses their identity. An
uncertain POST offers a same-key retry, never a fresh duplicate submission. Existing
postpaid UI flows and their uncertain-write behavior remain unchanged. Pin bodies,
headers and no-binary-float encoding in frontend/backend tests; exercise UI in both
configured time zones. MCP keeps read/write scopes and decimal-string amounts.

## Development acceptance and hand-off

Implement N1..N6 first, commit/push/deploy numbers only with the normal separate
approvals. Then develop schema/funding and API/worker behind unavailable prepaid
creation, followed by UI and release validation. N1 contract vendoring and fixture
validation are implementation prerequisites, not unfinished business design.
Update progress scope as features enter development; do not mark designed features
implemented or regenerate deployed percentages in this documentation-only change.

Required executable proofs in addition to the original acceptance list:

- Exact funds/one cent short with tax, zero-price periods and every cadence, month
  ends/leap restoration, DST, authorizations either side of midnight.
- Worker returns on the due date versus next date, misses several intervals with
  ample old funds, receives a top-up during recovery: only one new interval, no gap
  charges/retroactive entitlement, unchanged postpaid historical replay.
- Duplicate POSTs with identical/different keys/bodies, concurrent duplicate keys,
  rollback and deletion tombstones; command retries before/after each financial write.
- Held source versus explicit/automatic settlement, payouts, debit, opposite transfers
  and renewals; database constraints tested at commit, plus actual deadlock races.
- Final transfer behavior, both fee owners, receiver fee equal to principal, failure
  after acceptance, immutable originals and exactly-once reversal of eligible actions.
- Scheduled/future number claims, requirement changes after order history, locks and
  quarantine configuration changes; no duplicate assignment through cancellation.
- Mixed prepaid/postpaid financial-account reconciliation, settled offCycle documents,
  direct-payment top-up history exactly once, unchanged daily counts/status/cutoffs.
- Raw schema validation except the single conditional history defect; no fictitious
  receiver data, event payloads against the declared listener schemas, gap lock updates.
- Upgrade populated history as database owner; refusal of lossy downgrades and startup
  protection against exposing prepaid creation before the complete core is enabled.

Run the project-required negative proofs, lint, three full backend passes for a slice,
frontend checks and browser tests when implementing. Record actual evidence then.
No runtime code, migrations, vendored specs or deployment are part of this design
commit. The remaining work is implementation and its verification; there are no
open policy choices requiring another design session.
