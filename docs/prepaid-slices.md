# Prepaid slices: tracker

The order of work and progress for prepaid. **What** to build is in
[prepaid-plan.md](prepaid-plan.md) and [prepaid-implementation.md](prepaid-implementation.md).
**How it was built** is in [decisions/prepaid.md](decisions/prepaid.md), with one entry per
slice. This file says only **which slice is next and when it is done**.

Rules:

- One row per commit. Each slice commit updates its own row (status, commit is the
  `git log` subject) and its decision entry. Nothing else marks a slice done.
- A slice is done when its "Done when" line holds and has tests. A new test must fail
  without the slice (CLAUDE.md).
- `ratingType: prepaid` returns 422 until **P5a**. Before P5a, tests build prepaid
  accounts with factories, as they do now.
- If a slice proves too large, split it into `P3g1`/`P3g2` rows here. Do not widen
  its scope.
- Re-estimate at each **checkpoint** row and record the new range below.

Status: `done` · `next` · `todo` · `split` (see the sub-rows)

## Done (2026-10-20 .. 2026-10-21)

| Slices | Result |
|---|---|
| N1–N6 | Subscriber numbers: TMF639 inventory, assignment through orders, UI, release evidence. Shipped. |
| P1 | Account mode, stable bucket/accumulation identities (`0034`). |
| P2a–P2b6 | Shared funding core, retained holds, hold conversion, consuming paths that respect held sources (`0035`–`0037`). |
| P2c1–P2c3b4 | Command journal, command runs, funded intervals, atomic single- and multi-product posting, delayed starts, actual-date renewal recovery (`0038`–`0041`). |
| P2c4a–P2c4b | Worker dispatch with technical retries; retained funding shortfalls (`0042`). |

What exists: the billing owner can **post** a prepaid purchase or renewal exactly once
from a queued command. What is missing: nothing **creates** commands. No order,
renewal or action produces them, no product state reflects them, no public API
exposes balances, and daily billing does not yet know about prepaid accounts.

## Remaining

### P3: Lifecycle: orders and renewals drive commands

| ID | Slice | Done when | Status |
|---|---|---|---|
| P3a | Daily-run isolation | The daily run posts no recurring ABR or `onCycle` bill for a prepaid account's products. On-demand bills on prepaid collect only existing unbilled ABRs and otherwise reject. Postpaid counts, cutoffs and replay are unchanged. | done: Isolate prepaid accounts from daily recurring billing |
| P3b | Entitlement and suspension causes | Migration for a persisted manual-suspension cause. One effective-state function (terminated > manual/account suspended > active inside a grant > pendingActive > funding-suspended) uses the injected clock. Internal product view adds `authorizedAt`, `paidThrough`, cause, `needs_topup`. The worker stores and emits a funding-suspended transition when it observes one; GET emits nothing. | next |
| P3c | Immediate prepaid start | TMF622 POST on a prepaid account either rejects `insufficientFunds` (no hold, claim or product) or atomically holds the tax-inclusive total, claims numbers, creates `pendingActive` products and queues one command, with the order `inProgress`. The worker's posting completes items, products and order with TMF622/637 events. Multi-item orders are all-or-nothing. | todo |
| P3d | Future starts and pre-grant cancellation | A future order claims numbers with no hold; at the due date the command authorizes from that day's funds and price; a shortfall leaves it pending with a reason. `cancelProductOrder` before the first grant cancels the command and releases holds and claims; after a grant it returns 409. | todo |
| P3e | Renewal and resumption producer | Due renewals are produced in due-date, then product order, under the account lock. A shortfall makes the product funding-suspended at `paidThrough`. A later credit (payment now; top-up after P4d) resumes on the next tick, with a once-per-business-date rescan as the fallback. Manual suspension and Suspended/Closed accounts block renewal. | todo |
| **✓** | **Checkpoint 1**: purchase → renew → run out → top up → resume works end to end in tests. Re-estimate P3f–P7. | | |
| P3f | Manual suspend/resume and account state | A suspend order keeps the paid interval. Resume reuses a live grant or queues authorization from today. Suspended accounts allow only cleanup orders. Closing an account with held money, unfinished commands or live products returns 409. | todo |
| P3g | Termination credits | Termination at the requested business date credits each affected charge's unused dates with the original net/tax rounding, at most what is still uncredited. The billing owner writes the credit ABR, the `offCycle` credit document and the source credit. Owned holds are released once and the number is released. A same-day termination is a full credit; an expired interval gets none. *Highest uncertainty: split g1 (credit primitive) / g2 (order integration) if needed.* | todo |
| P3h | Offering change at renewal | A change order is accepted only at `paidThrough` (409 otherwise). It is persisted as a pending change and applied only by a funded renewal. Without funds it stays pending and suspended, with no fallback to the old plan. Resumption uses the new offering. Number and offering changes are allowed together at the boundary. | todo |
| P3i | Internal command operations | GET `/internal/v1/prepaid/command`, GET `/{id}` and POST `/{id}/retry` (202, or 409 unless `failedTechnical`), in the internal contract, with read/write MCP tools. `make docs`. | todo |

### P4: TMF654 balances and actions

| ID | Slice | Done when | Status |
|---|---|---|---|
| P4a | Action and source schema | Migration: `balance_action` (kind, status, checked request, amounts, reason, operator, command link, unique reversal) plus adjustment, transfer and reversal source kinds inside the combined source bounds. Ledger L and remaining/reserved values match the funding-proofs equation in property tests. | todo |
| P4b | Idempotency journal | Migration plus helper: operator/method/route/key, normalized request hash, stored response, tombstones. Same key and body replays; a different body is 409; concurrent duplicates give one action; a rollback leaves no key. Wired to prepaid order POST. | todo |
| P4c | Balance read API | TMF654 `bucket` and `accumulatedBalance` list/retrieve and `/hub`. TMF666 `currentBalance` includes the new entry kinds. Responses validate against the vendored schema. | todo |
| P4d | Top-ups | `topupBalance`: all five operations, with its TMF676 payment in the same transaction. A direct `done` payment on a prepaid account creates exactly one linked top-up. Cancelling an unspent, unheld top-up appends a reversal (409 otherwise). Create, cancel and failure events. Idempotency-Key on both POSTs. | todo |
| P4e | Adjustments | `adjustBalance` credit/debit. A debit is limited to spendable funds and consumes specific sources. Cancellation restores the exact sources once. | todo |
| P4f | Operator holds | Client-created `reserveBalance`: optional expiry, released before any later spend under the lock (API or worker). Owned holds return 409 here. | todo |
| P4g | Transfer acceptance | POST validates both sides under sorted account locks, holds principal (+ originator fee) and queues one command. Business refusals are stored `failed` (`sameBucket`, mode, currency, number). No principal moves. | todo |
| P4h | Transfer completion | The worker moves principal, posts the fee charge and settled `offCycle` bill for either fee owner (receiver fee ≤ principal), and completes the action atomically. Revalidation failure fails it and releases holds. No cancellation. Deadlock races in both directions. | todo |
| P4i | History, events, refunds | `balanceActionHistory` lists every action once (with the single documented schema exception). All 12 event names are registered and payloads validate. Prepaid cash refunds are limited to funds left after holds. The gap lock and `tests/tmf-gaps.txt` are updated. | todo |
| **✓** | **Checkpoint 2**: every TMF654 operation is live behind factories. Re-estimate P5–P7. | | |

### P5–P7: Enablement, UI, release

| ID | Slice | Done when | Status |
|---|---|---|---|
| P5a | Enable prepaid accounts | TMF666 POST/PATCH accept `prepaid` (PATCH only before history); 622/637/678 references match; a positive `creditLimit` is 422; internal previews show required/available/shortfall. Postpaid suite unchanged. | todo |
| P5b | MCP for prepaid | Read/write tools for TMF654 and previews, an explicit `idempotency_key` argument, decimal strings. `make mcp-smoke` covers one top-up and one purchase. | todo |
| P6a | UI: mode and prepaid card | Mode selected at creation and shown in a filter. The card shows ledger, available, reserved, paid-through and shortfall cause, kept distinct. Both time zones. | todo |
| P6b | UI: top-up, adjustment, hold | Forms with one Idempotency-Key per submission and a same-key retry on an uncertain response. Bodies pinned in frontend and backend tests. | todo |
| P6c | UI: transfer, pending, retry | Transfer by number with a finality confirmation. Pending order/transfer polling (5 s, 3 min cap). Technical retry is shown separately from needs-top-up. | todo |
| P7 | Release validation | Mixed-mode demo seed; `make check` + `web-e2e`; recovery runbook and the migration rollback barrier (`0034`+) documented; release evidence file like the subscriber-number one. | todo |

## Estimate

| Date | Remaining | Note |
|---|---|---|
| 2026-10-21 | 24 rows, expect 24–30 commits | Past slices split roughly 1.3× (P2c3b → 4). Most likely splits: P3c, P3g, P4h. |
