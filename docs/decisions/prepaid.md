# Subscriber numbers and prepaid: finalized design (2026-10-20)

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
exactly one number is funded. Reads never create a bucket; until P5a only factories
write `prepaid_bucket`.

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
forever. The header is optional and wired to `POST /productOrder` (any account mode);
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
a prepaid account without a bucket (test fixtures until P5a) records none. `post_payment`
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
price changes), not on every tick. P4d top-ups must join that credit signal. Manual
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

- [Final scope and acceptance criteria](../prepaid-plan.md).
- [Implementation design, contract pins and policy decisions](../prepaid-implementation.md).

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

Release evidence is in [the validation record](../subscriber-number-release.md).
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
so an accepted transfer waits `created`, holding its money, until then. The funds check
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
it stays `created` and queued, holding its money, until P4h2. Sender number/product
ownership is not re-checked at completion (acceptance checked it; neither can move away
while the product lives, and the money, not the number, is what moves).

P4h2 (migration `0047`): nullable `applied_customer_billing_rate.product_id`, new
`charge_type` `oneTime`, a unique nullable `balance_action_id` foreign key, a one-day
half-open period, checks scoped by charge type, TMF678 omitting `product` when null,
internal mappers that treat non-recurring as refund fixed, and a bill presentation line
without a product. It will then post the fee charge and the settled `offCycle` bill in
the same completion. Tests: `tests/test_transfer_completion.py`.
