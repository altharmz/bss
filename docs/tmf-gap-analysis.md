# TMF conformance gap analysis

## Where the truth is

[`tests/tmf-gaps.txt`](../tests/tmf-gaps.txt) is the test-locked list of attribute gaps
and accepted deviations with their reasons, produced by the probe in
[`tests/tmf_gaps.py`](../tests/tmf_gaps.py). The generated [API reference](api/README.md)
lists every served operation, cross-checked against `spec/`. The rule is
[implement what the specs define, skip the rest](decisions/tmf-api.md#following-tmf-2026-10-17).

## What the probe cannot see

The behaviour gaps below were closed or recorded as not defined by the specs on
2026-10-19:

- States: order item actions `modify`/`noChange` - executed (slices 8a/8b);
  order `draft` - implemented (8a); `held`, `pending`, `failed`, `partial`,
  `inProgress.accepted`, `assessingCancellation`, `pendingCancellation` - asynchronous
  processing, never reached here (recorded, 8a); product `suspended` - reached
  ([suspension](decisions/tmf-api.md#suspension-2026-10-19)), `pendingActive`, `aborted`
  - asynchronous provisioning, never reached (recorded); C9 - closed (ends, suspensions
  and resumptions on any account); payment states other than `done` - TMF676 v4 defines
  none (free text, no transitions); party `status` - writable since slice 4 (the item was
  stale).
- priceType `discount`, `allowance`, `penalty` - records, never billed
  ([catalog.md](decisions/catalog.md#alteration-price-types-2026-10-19)).
- Nested response attributes - probed one level deep since 2026-10-19.
