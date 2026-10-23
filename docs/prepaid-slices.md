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
- `ratingType: prepaid` is accepted since **P5a**; older tests still build prepaid
  accounts with factories.
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

What exists: immediate and future prepaid add orders reserve funds and queue commands; the
billing owner posts them exactly once and completes the full product/order tree.
The worker produces renewals at paid-through; a shortfall suspends the product and
a later credit resumes it. Entitlement follows funded coverage, independently of
manual/account suspension; daily recurring billing excludes prepaid. Manual
suspend/resume orders keep the paid interval and reuse or renew the grant; closing
a prepaid account requires everything ended. Termination orders end products on
their date; the billing owner credits unused dates with a settled credit note. An
offering change waits for the next renewal and applies only when funded (`0044`). What is missing:
MCP tools, UI and release validation (P5b-P7). Prepaid accounts can be created
through TMF666 since P5a.

## Remaining

### P3: Lifecycle: orders and renewals drive commands

| ID | Slice | Done when | Status |
|---|---|---|---|
| P3a | Daily-run isolation | The daily run posts no recurring ABR or `onCycle` bill for a prepaid account's products. On-demand bills on prepaid collect only existing unbilled ABRs and otherwise reject. Postpaid counts, cutoffs and replay are unchanged. | done: Isolate prepaid accounts from daily recurring billing |
| P3b | Entitlement and suspension causes | Migration for a persisted manual-suspension cause. One effective-state function (terminated > manual/account suspended > active inside a grant > pendingActive > funding-suspended) uses the injected clock. Internal product view adds `authorizedAt`, `paidThrough`, cause, `needs_topup`. The worker stores and emits a funding-suspended transition when it observes one; GET emits nothing. | done: Project prepaid entitlement and independent suspension causes |
| P3c | Immediate prepaid start | TMF622 POST on a prepaid account either rejects `insufficientFunds` (no hold, claim or product) or atomically holds the tax-inclusive total, claims numbers, creates `pendingActive` products and queues one command, with the order `inProgress`. The worker's posting completes items, products and order with TMF622/637 events. Multi-item orders are all-or-nothing. | done: Accept and complete immediate prepaid purchase orders |
| P3d | Future starts and pre-grant cancellation | A future order claims numbers with no hold; at the due date the command authorizes from that day's funds and price; a shortfall leaves it pending with a reason. `cancelProductOrder` before the first grant cancels the command and releases holds and claims; after a grant it returns 409. | done: Schedule prepaid purchases and cancel before first grant |
| P3e | Renewal and resumption producer | Due renewals are produced in due-date, then product order, under the account lock. A shortfall makes the product funding-suspended at `paidThrough`. A later credit (payment now; top-up after P4d) resumes on the next tick, with a once-per-business-date rescan as the fallback. Manual suspension and Suspended/Closed accounts block renewal. | done: Produce prepaid renewals and resume after a credit |
| **✓** | **Checkpoint 1**: purchase → renew → run out → top up → resume works end to end in tests. Re-estimate P3f–P7. | | done: `test_purchase_renew_run_out_top_up_resume` (payment as the credit until P4d) |
| P3f | Manual suspend/resume and account state | A suspend order keeps the paid interval. Resume reuses a live grant or queues authorization from today. Suspended accounts allow only cleanup orders. Closing an account with held money, unfinished commands or live products returns 409. | done: Suspend and resume prepaid products by order |
| P3g | Termination credits | Termination at the requested business date credits each affected charge's unused dates with the original net/tax rounding, at most what is still uncredited. The billing owner writes the credit ABR, the `offCycle` credit document and the source credit. Owned holds are released once and the number is released. A same-day termination is a full credit; an expired interval gets none. *Highest uncertainty: split g1 (credit primitive) / g2 (order integration) if needed.* | done: Credit unused prepaid dates on termination |
| P3h | Offering change at renewal | A change order is accepted only at `paidThrough` (409 otherwise). It is persisted as a pending change and applied only by a funded renewal. Without funds it stays pending and suspended, with no fallback to the old plan. Resumption uses the new offering. Number and offering changes are allowed together at the boundary. | done: Change prepaid offerings at the funded renewal |
| P3i | Internal command operations | GET `/internal/v1/prepaid/command`, GET `/{id}` and POST `/{id}/retry` (202, or 409 unless `failedTechnical`), in the internal contract, with read/write MCP tools. `make docs`. | done: Add internal prepaid command list, detail and retry |

### P4: TMF654 balances and actions

| ID | Slice | Done when | Status |
|---|---|---|---|
| P4a | Action and source schema | Migration: `balance_action` (kind, status, checked request, amounts, reason, operator, command link, unique reversal) plus adjustment, transfer and reversal source kinds inside the combined source bounds. Ledger L and remaining/reserved values match the funding-proofs equation in property tests. | done: Add balance action and adjustment/transfer/reversal source schema |
| P4b | Idempotency journal | Migration plus helper: operator/method/route/key, normalized request hash, stored response, tombstones. Same key and body replays; a different body is 409; concurrent duplicates give one action; a rollback leaves no key. Wired to prepaid order POST. | done: Add the Idempotency-Key response journal |
| P4c | Balance read API | TMF654 `bucket` and `accumulatedBalance` list/retrieve and `/hub`. TMF666 `currentBalance` includes the new entry kinds. Responses validate against the vendored schema. | done: Serve TMF654 buckets, accumulated balances and the hub |
| P4d | Top-ups | `topupBalance`: all five operations, with its TMF676 payment in the same transaction. A direct `done` payment on a prepaid account creates exactly one linked top-up. Cancelling an unspent, unheld top-up appends a reversal (409 otherwise). Create, cancel and failure events. Idempotency-Key on both POSTs. | done: Record top-ups and the top-up each direct payment makes |
| P4e | Adjustments | `adjustBalance` credit/debit. A debit is limited to spendable funds and consumes specific sources. Cancellation restores the exact sources once. | done: Add balance adjustments, credit and debit |
| P4f | Operator holds | Client-created `reserveBalance`: optional expiry, released before any later spend under the lock (API or worker). Owned holds return 409 here. | done: Add operator holds, reserveBalance |
| P4g | Transfer acceptance | POST validates both sides under sorted account locks, holds principal (+ originator fee) and queues one command. Business refusals are stored `failed` (`sameBucket`, mode, currency, number). No principal moves. | done: Accept transfers, transferBalance |
| P4h1 | Transfer completion, no fee | The worker moves principal of a fee-free transfer (consumes the hold's sources, adds the receiver's `transfer` source) and completes the action atomically under the command-identity lock then both account locks in id order. Revalidation failure fails it and releases the hold. No cancellation. Deadlock races in both directions. No migration. | done: Complete fee-free transfers in the worker |
| P4h2 | Transfer fee posting | Migration `0047`: nullable `applied_customer_billing_rate.product_id`, new `charge_type` `oneTime`, unique nullable `balance_action_id` FK, one-day half-open period, checks scoped by charge type; TMF678 omits `product` when null; internal mappers stop treating non-recurring as refund; bill presentation line without a product. The worker then posts the fee charge and settled `offCycle` bill for either fee owner (receiver fee ≤ principal) in the same completion; fee transfers wait queued until then. | done: Post transfer fees as one-time charges |
| P4i | History, events, refunds | `balanceActionHistory` lists every action once (with the single documented schema exception). All 12 event names are registered and payloads validate. Prepaid cash refunds are limited to funds left after holds. The gap lock and `tests/tmf-gaps.txt` are updated. | done: Add balanceActionHistory and pin events and refunds |
| **✓** | **Checkpoint 2**: every TMF654 operation is live behind factories. Re-estimate P5–P7. | | done: P4a-P4i took 11 commits for 9 rows (P4h split in two); estimate recorded 2026-10-23 below |

### P5–P7: Enablement, UI, release

| ID | Slice | Done when | Status |
|---|---|---|---|
| P5a | Enable prepaid accounts | TMF666 POST/PATCH accept `prepaid` (PATCH only before history); 622/637/678 references match; a positive `creditLimit` is 422; internal previews show required/available/shortfall. Postpaid suite unchanged. | done: Enable prepaid accounts on TMF666 |
| P5b | MCP for prepaid | Read/write tools for TMF654 and previews, an explicit `idempotency_key` argument, decimal strings. `make mcp-smoke` covers one top-up and one purchase. | done: Add idempotency_key to prepaid MCP tools |
| P6a | UI: mode and prepaid card | Mode selected at creation and shown in a filter. The card shows ledger, available, reserved, paid-through and shortfall cause, kept distinct. Both time zones. | done: Add account mode and prepaid card to the UI |
| P6b | UI: top-up, adjustment, hold | Forms with one Idempotency-Key per submission and a same-key retry on an uncertain response. Bodies pinned in frontend and backend tests. | todo |
| P6c | UI: transfer, pending, retry | Transfer by number with a finality confirmation. Pending order/transfer polling (5 s, 3 min cap). Technical retry is shown separately from needs-top-up. | todo |
| P7 | Release validation | Mixed-mode demo seed; `make check` + `web-e2e`; recovery runbook and the migration rollback barrier (`0034`+) documented; release evidence file like the subscriber-number one. | todo |

## Estimate

| Date | Remaining | Note |
|---|---|---|
| 2026-10-21 | 24 rows, expect 24–30 commits | Past slices split roughly 1.3× (P2c3b → 4). Most likely splits: P3c, P3g, P4h. |
| 2026-10-23 | 19 rows, expect 19–23 commits | Checkpoint 1: P3a–P3e took one commit each, no splits. P3g and P4h remain the likely splits. |
| 2026-10-23 (checkpoint 2) | 6 rows after P5a (P5b, P6a-c, P7), expect 6-8 commits | P4a-P4i took 11 commits for 9 rows (only P4h split). P5a took one commit, no migration. P6b and P6c (idempotent forms, polling) are the likely splits; P5b and P7 stay single. |
