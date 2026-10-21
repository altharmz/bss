# Subscriber numbers and prepaid: finalized design (2026-10-20)

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
