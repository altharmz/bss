# Refunds and bill delivery milestone (2026-10-16)

Approved as three implementation slices: TMF676 refunds (3 operations), TMF678
on-demand bills (3), TMF666 formats/presentation media (8). Total: 67/129 non-listener
TMF operations (51.9%). This is endpoint coverage, not full conformance.

## Recorded payouts

`refund` records completed payouts, never invokes an external payment processor.
`refund_source` consumes unused credit from payments first, then issued credit notes,
in the same order as settlement. Optional `payment` confines sources to that payment.
Both are immutable. Money is positive, exact cents, in the account currency; the
server clock dates the payout. The payment method name and type are preserved.

POST holds the billing account row lock, settles first, and permits no more than
the lesser of unused source credit and positive net account balance (unbilled ABRs
included). A refused request rolls back the entire transaction. This prevents a
payout while debt remains, including held bills. Closed accounts may receive refunds.
Deferred source-total and combined bill-allocation/refund-source bounds protect
against double spending; BEFORE INSERT locks serialize these writes even from SQL.
Balances subtract completed payouts; settlement and unused-credit views exclude
consumed sources. The ledger calls this debit `payout`, distinct from an ABR credit
(`refund`), and the UI labels it “Refund paid out”.
For cash ledger entries sharing a timestamp, payments precede payouts; different
timestamps keep chronological order. The final running balance is always the account
balance, including under a frozen clock.

## On-demand bill requests

POST creates `inProgress`, never bills. One pending request per account (409 otherwise).
The billing owner processes requests after its ordinary request queue. It waits for
an `ok` account outcome at or after the day following the request, so termination
orders completed on the request day are visible under the existing midnight cutoff.
Failed/blocked/missing account runs leave the request pending for a later tick.

The worker collects all posted unbilled ABRs through that run date, issuing and
settling one `offCycle` bill in the same transaction as the request becomes `done`.
There is no zero-charge placeholder bill: no remaining ABRs means `rejected` with
an explanation. The bill links to the account run that supplied its billing horizon.
It covers [run date, next account cycle day), and does not advance the monthly
anchor. Monthly exclusion constraints apply to onCycle bills; ABRs remain singly
attached. The core sees monthly bills only, avoiding skipped future monthly bills.
Replaying the worker cannot produce a second bill for a completed request.

This is asynchronous, not a real-time charge calculation: a final bill becomes
available after the daily run has recognized the termination adjustment. A delayed
worker catches up first. Existing run ownership and account locks still apply.

Migration 0011 refuses downgrade while off-cycle financial history exists; removing
it requires a separately reviewed data migration. Earlier migrations are immutable.

## Presentation configuration and documents

BillFormat supports two real renderers, `JSON detailed` (charges and totals) and
`JSON summary` (totals and dates). BillPresentationMedia describes preferences; since #202 a
medium named `email` emails each new bill with its PDF ([bills.md](bills.md#pdf-documents-and-email-delivery-202-2026-11-18)),
and nothing else sends postal mail or initiates any other delivery.
Both expose list/retrieve/create/PATCH, name and optional description, strict fields
and equality name filters. Names are unique; unsupported formats are a 422.

Account `billStructure.format` and `presentationMedia` accept existing references.
Nested merge-patch preserves unspecified preferences; format null restores default
detailed JSON, media []/null clears media. CycleSpecification remains read-only;
the entire billStructure cannot be removed. Updates hold the account row lock.

Every newly issued bill snapshots a base64 JSON Attachment in `billDocument`,
including the selected format, media descriptions, account, dates, exact totals and
(for detailed output) charge details. Settlement/configuration/name changes do not
rewrite the document. Legacy bills keep no document rather than invented history.
The TMF bill response serves the inline attachment; since #202 it also lists the bill's PDF
by url (bills.md).

Migration 0010 adds two refund tables, 0011 one request table, and 0012 three
presentation tables plus account/document columns. Existing money and dates are
unchanged. The human separately authorized commit, push and a deployment attempt
on 2026-10-16.

## Local validation

Three sequential full backend runs passed: 913 tests in dev, CI, then dev profiles.
The progress collector also passed all 913 tests (with its two existing in-process
pytest warnings) and all 226 UI tests. Frontend lint/types/formatting passed, as did
226 component tests in each configured time zone and both Playwright smoke tests.
The local development database migrated through `0012`.

Negative proofs removed payout consumption from settlement, altered an off-cycle
bill total, and forced detailed rendering for a summary configuration. Four test
cases failed as expected; all mutations were restored. The first full run also
caught a timestamp-tie ledger ordering bug, fixed with an explicit cash-event tie
breaker and a deterministic UUID-order regression test.

Generated progress: database 28/28, API 83/83, UI 19/19 and worker 14/14 implemented
and tested; no unscoped items. TMF non-listener operations are 67/129 (51.9%).
The human approved and applied the documentation generator and API-note changes,
including the earlier catalog notes, on 2026-10-16. Reference pages are regenerated
from the current API and migration head before committing.
