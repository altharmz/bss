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
