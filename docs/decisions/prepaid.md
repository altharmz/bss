# Subscriber numbers and prepaid: finalized design (2026-10-20)

The human requested completion of the prepaid design, choosing remaining policies
according to TMF, then committing/pushing design changes. Development will occur in
another session. The authoritative hand-off is:

- [Final scope and acceptance criteria](../prepaid-plan.md).
- [Implementation design, contract pins and policy decisions](../prepaid-implementation.md).

Subscriber numbers ship independently before prepaid. The design selects TMF639
v5.0.0 and stable TMF654 v4.0.0, including explicit contract deviations. These specs
are pinned in the design but are not yet vendored. This record does not change the
current runtime: accounts remain postpaid until the complete funding core is ready.

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
