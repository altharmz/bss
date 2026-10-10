# Bill lifecycle (option C)

Decision records moved from CLAUDE.md (as of `910ec0a`), unchanged in meaning. The rules
needed every session stay in [CLAUDE.md](../../CLAUDE.md); `scripts/check-decisions.py`
proves nothing was lost in the move. Index: [README.md](README.md).

## Bill lifecycle (option C, in progress)

Design and decisions: **`docs/bill-lifecycle-design.md`**, section 6 (decided
2026-10-10: option C, payment term 30 days configurable, credit notes pay older open
bills, overdue is display only, a Record payment dialog). Four slices: 1. schema +
settlement core (`cloud/bill-settlement-core`), 2. worker (`cloud/bill-settlement-worker`,
stacked on 1), 3. API (`cloud/bill-settlement-api`, stacked on 2), 4. UI + seed
(`cloud/bill-settlement-ui`, stacked on 3). **All four slices are built and merged**
(`1d7a242`, 2026-10-10; not deployed yet). Slice 4: the subscriber card's Bills panel, bill dialog with hold/release,
Record payment with optional lettering, "Due on bills" / "Unused credit" KPIs
(`screens/Bills.tsx`, details in `docs/ui.md`); TMF678 types are generated now
(`gen-types.mjs`); several exact amounts in one body via `jsonWithAmounts`; the seed puts
Olga's oldest bill on hold and letters a Sergey payment to his newest bill
(`test_seed.py` asserts every state and both allocation kinds); `test_ui_requests.py`
replays the payment and PATCH bodies; the e2e opens the held bill (read-only). Negative
proofs: payment amount as a float, prefill newest first. Slice 3: payments settle at once and take explicit
`paymentItem`s (see ["TMF676 (v4) mapping"](tmf-api.md#tmf676-v4-mapping)), bills carry remainingAmount, paymentDueDate,
appliedPayment, billNo, runType, category and are held/released by PATCH (see ["TMF637
and TMF678 mapping"](tmf-api.md#tmf637-and-tmf678-mapping)), and two internal endpoints (settlement summary, bill detail; see
["Internal API"](internal-api.md#internal-api-stage-5)). `tests/test_api_bill_settlement.py`; negative proofs: no settlement in
POST /payment, no settlement on release, no explicit lock in POST /payment (fails only
the payment-vs-payment test - the foreign key already serializes a payment with a run).

- **The run issues and settles** (slice 2, `bill_account`, in the account's one
  transaction after `apply_result`): `issue_bills` takes every bill of the account still
  `new` or `validated` (its own, and any left `new` from before 0006 - the back-fill
  needs no data migration), validates it (`settlement.validate`: amount_due = the sum of
  its ABRs, at least one ABR, period = [bill_date, next cycle day); a failure is
  `InconsistentState`, so the account fails in the run and nothing of it is written),
  then new -> validated -> sent, and -> settled when amount_due <= 0 (zero bills and
  credit notes). Then `settle_account` applies the unused credit (cause `run`, dated by
  the attempt's `started_at`). Every run settles every member account, so a payment is
  applied by the next run even if it issues no bill. `write_settlement` sets the bounds
  trigger IMMEDIATE once it has written allocations, so a breach fails inside the
  account's savepoint, not at the worker's commit. History rows of one run share
  `changed_at`; `seq` (identity) orders them.
- Proven (`tests/test_bill_lifecycle_db.py`): issue steps and idempotence; the stage 3
  example 4 credit note (-116.13) settled at issue and paying the Dec bill down to 183.87;
  a payment before the first bill settling it at issue, the rest carried to January, a
  later payment applied by a run without a bill; a bill left `new` issued and settled by
  the next run; tampered amount or period failing the account with nothing written; the
  6.5 invariant after each step. The replay test now also compares state, due date and
  category. Negative proofs: validation skipped, no settlement in the run, credit notes
  not settled at issue, only this run's bills issued - each failed a test.

- **Migration 0006**: `customer_bill.payment_due_date` (bill_date + the term in force when
  the bill is written; existing bills + 30, a literal) and `category` (`normal` |
  `creditNote`, creditNote iff amount_due < 0, a CHECK); `payment` UNIQUE (id,
  billing_account_id); **`bill_allocation`** (what paid a bill: a payment or a credit
  note, `automatic` | `explicit`, append-only, composite FKs keep everything in one
  account and currency) with a **deferred constraint trigger** for the bounds that span
  rows (per paid bill <= amount_due, per payment <= amount, per credit note <=
  -amount_due; only a positive bill is paid, only a negative one gives credit) - checked
  at commit, so `rejected(...)`'s savepoint cannot catch it: test with `conn.commit()`;
  **`customer_bill_state_change`** (from, to, changed_at by the injected clock, cause
  run | payment | operator, operator only for operator). Existing bills get their issue
  row (NULL -> new) dated by their run's `started_at`; new bills get it from `apply_result`
  (`issued_at` = the running attempt's `started_at`).
- **`BILL_PAYMENT_TERM_DAYS`** (`app/config.py`, 1..365, default 30): read by the worker
  when it writes a bill; an invalid value fails settings validation at startup. Passed to
  neither service in `compose.prod.yaml` (like `BILLING_TZ`): changing it there is a
  compose change.
- **`app/billing/settlement.py`** (pure): `settle(bills, payments, explicit, payment_id)`
  applies the explicit items of one payment, then the oldest unused credit (payments by
  `received_at`, then issued credit notes by bill date) to the oldest open bill (`sent` /
  `partiallyPaid`, positive). `onHold`, `new`, `validated` and `settled` bills are never
  paid automatically; explicit items to them are refused (`BillNotOpen`, and
  `UnknownBill`, `DuplicateBill`, `ExceedsRemaining`, `ExceedsPayment`).
  `operator_change` / `operator_states`: hold from `sent`/`partiallyPaid`; release only to
  the state the allocations imply (strict: anything else is `TransitionRefused` naming it).
- **Repository**: `load_settlement` (bills with allocated / credit used, payments with
  used; call it under `load_account`'s row lock), `write_settlement` (allocations, state
  changes with history rows, touches every paid bill so `lastUpdate` moves),
  `record_bill_state` (refuses if the bill left `from_state`), `bill_state_history`.
- **Tests**: `tests/test_settlement.py` (examples, hold/release table, hypothesis: the
  remaining per bill equals the cumulative rule of the design's 3.0 whether payments
  settle at once or only in runs; no open bill and unused credit at once; holds skipped;
  explicit items exact), `tests/test_settlement_db.py` (round trip, trigger bounds at
  commit, FKs, CHECKs), `test_0006_on_existing_bills`. Negative proofs, each made once
  and reverted: newest-first allocation, holds not skipped, credit notes ignored,
  release to any open state, the trigger's bill bound doubled, the due date ignoring the
  configured term - each failed at least one test.
