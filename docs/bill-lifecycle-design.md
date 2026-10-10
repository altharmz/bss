# Bill lifecycle: design proposal

Status: **decided: option C** (2026-10-10, branch `cloud/bill-lifecycle-design`). Section
6 records the decisions and the refined design to build; sections 1-5 are the proposal
as reviewed. Nothing here is implemented. Written against `master` at `209955c`.

Problem: every `customerBill` stays in state `new` for ever, and payments never touch a
bill. Payments only move the account balance. This document records what the code does
today (section 1), the TMF678 state model (2), three options (3), edge cases (4), and a
recommendation with the questions you need to answer (5).

Citations are `path:line` at `209955c`.

---

## 1. Current state

### 1.1 How bills are created

- **Only the billing run writes bills.** The worker's `DateRun.step()` calls
  `bill_account(conn, account_id, run)` once per member account and date
  (`app/worker/process.py:134`). `bill_account` locks the billing account row
  (`app/repository/billing.py:570`, `SELECT ... FOR UPDATE`), loads the state, runs the
  pure core and writes the result in one transaction (`app/billing/adapter.py:52-64`).
- **Bill day and period.** The core's `_bills` (`app/billing/core.py:234-272`) issues one
  bill per cycle day D after the account's last bill, up to the run date (`as_of`). It
  covers `[D, same day next month)` (`core.py:261-266`). The module docstring says the
  same (`core.py:10-12`).
- **Which ABRs.** A bill on D collects every **unbilled** ABR whose **run date** is <= D
  (`core.py:238-259`; the `take` filter is at `core.py:258`). ABRs written in the run of D
  go onto the bill of D. A product that starts mid-cycle is charged in advance on its start
  date, but that charge goes onto the **next** bill:
  `test_mid_cycle_start_goes_onto_the_next_bill`, `tests/test_billing_core.py:216`
  (164.52 charged on Oct 15, billed on Nov 1 together with Nov's 300.00 = 464.52).
- **Empty vs zero.** A cycle day with nothing to collect gets no bill. A bill whose ABRs
  sum to 0.00 is still issued:
  `test_empty_bills_are_skipped_and_zero_bills_are_not`, `tests/test_billing_core.py:232`.
- **Negative bills.** `amount_due` is the plain sum of the collected ABRs. Refund ABRs are
  negative (`alembic/versions/0001_initial_schema.py`, constraint `abr_refund_amount`), so
  a bill can be negative: `test_bill_may_be_negative`, `tests/test_billing_core.py:223`
  (-116.13). The column comment says so too: "may be negative"
  (`alembic/versions/0005_schema_comments.py:289`).
- **Missed cycle day.** A run whose `as_of` is past a cycle day that has no bill yet
  issues the bill for that day, with what was known by then:
  `test_missed_cycle_day_is_billed_with_what_was_known_by_then`,
  `tests/test_billing_core.py:240`. In production the worker replays missed dates in
  order, each with its own run, so the bill of D is written by the run of D, even when
  that run executes days later. `test_replay_after_an_outage_gives_the_same_rows_and_bills_as_daily_runs`,
  `tests/test_billing_db.py:285`, proves this.
- **Every bill collects every ABR exactly once**: property
  `test_bills_collect_every_abr_exactly_once`, `tests/test_billing_properties.py:278`.
- **Schema** (`alembic/versions/0001_initial_schema.py:326-342`): `customer_bill`
  (`billing_account_id`, `currency`, `bill_period daterange`, `bill_date`,
  `state customer_bill_state_type NOT NULL`, `amount_due numeric(12,2)`,
  `billing_run_id`). Bills of one account never overlap (`customer_bill_no_overlap`). An
  ABR points at its bill through `customer_bill_id` (NULL = unbilled).
- **State is written once, as `new`.** `insert_bill(..., state: str = "new")`
  (`app/repository/billing.py:462-490`, default at `:471`). `write_bill` never passes a
  state (`billing.py:697-706`). Nothing in `app/` or `tests/` ever runs
  `UPDATE customer_bill` (grep finds none). The column comment says "bills are written as
  new" (`0005_schema_comments.py:288`).

### 1.2 What TMF678 exposes today

`app/api/bill.py`, read-only.

- Routes: `GET /customerBill`, `GET /customerBill/{id}`, plus the ABR pair
  (`app/api/bill.py:99-155`). POST and PATCH return 405:
  `test_bills_and_rates_are_read_only`, `tests/test_api_inventory_bill.py:180`.
- Attributes (`bill.py:28-35`, body at `:58-69`): `id`, `href`, `billDate`,
  `billingPeriod`, `amountDue`, `state` (the stored column, `:65`), `billingAccount`,
  `lastUpdate`, `@type: CustomerBill`.
- **Not exposed**: `remainingAmount`, `paymentDueDate`, `appliedPayment[]`, `billNo`,
  `category`, `runType`, `taxExcludedAmount`/`taxIncludedAmount`, `nextBillDate`.
- Filters: `billingAccount.id`, `state` (`bill.py:103-107`).
- The test checks `state == "new"` and that `?state=sent` returns nothing:
  `test_customer_bill_and_is_billed`, `tests/test_api_inventory_bill.py:132-168`
  (asserts at `:152` and `:168`).
- The DB enum holds all six TMF values (`0001_initial_schema.py:80-89`), and
  `tests/test_enum_lock.py:27` locks it against the spec.

### 1.3 How TMF676 payments are stored

- Table `payment` (`0001_initial_schema.py:383-393`, plus `payment_method` from
  `0002_tmf_api_columns.py:64`): `billing_account_id`, `currency` (composite FK to the
  account's currency), `amount > 0`, `received_at`, `status` (enum with the single value
  `done`, `0001:97`). There is no link to a bill.
- `POST /payment` (`app/api/payment.py:120-146`) records a payment that has already been
  received: `status` `done`, `paymentDate` = `statusDate` = the injected clock
  (`:138`), `totalAmount` > 0 (`:59`), in the account's currency. It takes **no lock**
  on the account. Test: `test_payment_is_recorded_as_done`,
  `tests/test_api_payment.py:20`.
- `paymentItem` (TMF676's link from a payment to bills) is not modelled. As an unknown
  attribute it would be a 400. The same rule is proven for `description` in
  `test_invalid_payments_are_rejected`, `tests/test_api_payment.py:88` (case at `:84`).
- Payments are never changed or deleted (405): `test_payments_cannot_be_changed`,
  `tests/test_api_payment.py:142`.

### 1.4 Balance and credit limit

- **Balance = payments (`done`) - sum of all ABRs, billed or not**
  (`app/repository/billing.py:493-511`). Positive means the customer is in credit. It is
  exposed as TMF666 `accountBalance[currentBalance]` (`app/api/account.py:168-174`).
  Test: `test_balance_includes_charges`, `tests/test_api_inventory_bill.py:171` (a
  100.00 charge gives -100.00).
- So **bills play no part in the balance**. An ABR moves the balance the day it is
  written, which is weeks before its bill in the mid-cycle case. Bills only group ABRs.
- `creditLimit` is informational only. It is shown, and `firstCharge` computes
  `exceedsCreditLimit = after < -limit` (`app/internal/accounts.py:142-170`). Nothing is
  refused because of it (CLAUDE.md, "creditLimit").
- The internal `transactions` ledger (`app/repository/billing.py:756-808`,
  `app/internal/accounts.py:274`) lists charges, refunds and payments with
  `balanceAfter`. Bills do not appear in it.

### 1.5 What the UI shows

- **No screen shows bills.** The subscriber card (`frontend/src/screens/SubscriberCard.tsx`)
  has a Billing account panel (`:219-262`: Balance with an "In arrears" caption when
  negative, Currency, Cycle day + next cycle date, Credit limit, Available incl. credit),
  then Charge history (ABRs: date, period, days, amount, `:36-41`, `:392-417`) and
  Transaction history (`:42-47`, `:418-440`). The internal `charges` endpoint returns a
  `bill` link per ABR (`spec/internal/bss-internal.oas.yaml:465`), but the UI does not
  render it (`CHARGE_COLUMNS`, `SubscriberCard.tsx:36-41`).
- The subscribers list shows the balance and counts negative balances
  (`frontend/src/screens/Subscribers.tsx:51`, `:82`).
- **The UI cannot record a payment.** Payments come only from TMF676 `POST /payment` and
  from the seed (`tests/seed.py:228-267`).

---

## 2. The TMF678 state model

From `spec/TMF678-CustomerBill-v5.0.0.oas.yaml:1753-1765`, `CustomerBillStateType`:

```
new | onHold | validated | sent | settled | partiallyPaid
```

The spec calls it a "Recommended Enumeration Type (not formal forced in standard)" and
gives these meanings: new = ready to validate or to send; validated = checked (manually or
automatically); sent = sent through the channel defined in the billing account; settled =
paid; onHold = no further processing until open issues are solved. **The description says
`partiallySettled`, but the enum value is `partiallyPaid`.** The enum is authoritative,
and `test_enum_lock.py` already compares against it.

The spec defines no transitions. It also has **no overdue, unpaid, written-off or
cancelled state**. Payment status and document status share one field.

Related spec fields:

- `CustomerBill` (`:1459` ff.): `remainingAmount` (Money), `paymentDueDate`
  (date-time), `appliedPayment[]` of `AppliedPayment {appliedAmount: Money, payment:
  PaymentRef}` (`:708-717`; "the result of lettering ... automatically or manually"; "an
  n:m relation between payment and bill"), `billNo`, `category` (free text, e.g. "credit
  note"), `runType` (`onCycle`/`offCycle`), `taxExcludedAmount`/`taxIncludedAmount`.
- `PATCH /customerBill/{id}` exists in the spec (`:248`). Its body is `CustomerBill_MVO`
  (`:1854-1870`), which allows **only `state` and `billCycle`**. In the spec, state is the
  one thing a client may change on a bill.
- TMF676 v4 (`spec/TMF676-Payment-v4.0.0.swagger.json`): `Payment.paymentItem[]` of
  `PaymentItem {item: EntityRef (required), amount, totalAmount, taxAmount}` (`:1499`
  ff.). It is the payment-side view of the same lettering. `PaymentStatusExampleType`
  (`:1738`) includes `done`. A separate `/refund` resource (`:1751`) pays money back to a
  customer. That TMF676 refund has nothing to do with our refund ABRs (credits on a bill).

### Which states make sense here

| State | Use in this demo |
|---|---|
| `new` | Issued, nothing paid yet. **Yes.** |
| `partiallyPaid` | Some, but not all, of `amountDue` is covered. **Yes.** |
| `settled` | Fully covered, or nothing was due (`amountDue <= 0`). **Yes.** |
| `validated`, `sent` | No validation step, bill document or delivery channel exists. They would be cosmetic. **Not now** (only option C). |
| `onHold` | Disputes. Useful only once someone can take a bill out of automatic settlement. **Not now** (only option C). |

"Overdue" is not a TMF state. If wanted, it is a derived flag:
`paymentDueDate < business date AND remainingAmount > 0`. It is not a state value.

---

## 3. Options

All three keep these unchanged:

- **The balance.** It stays payments - all ABRs. Bills still do not define it. Section
  3.0 explains how the two relate.
- **The pure billing core.** Settlement is a separate pure function. `compute()` is not
  touched.
- **Payments are append-only.**
- **Allocation is oldest-first.** Credit goes to the bill with the earliest `billDate`
  first. A newer bill is never partly paid while an older one is still open.

### 3.0 The rule all options share (settlement arithmetic)

For one account:

- *Positive bills*, ordered by `bill_date`, with amounts a1, a2, ... and cumulative dues
  `cum_k = a1 + ... + ak`.
- *Credits*: every `done` payment, plus `|amountDue|` of every negative bill.
  `C` = their total.
- Bill k: `remaining_k = min(a_k, max(0, cum_k - C))`. Its state is `settled` if
  `remaining_k = 0`, `new` if `remaining_k = a_k`, else `partiallyPaid`. A bill with
  `amountDue <= 0` is `settled` from the moment it is issued, with `remainingAmount` 0.
- *Unallocated credit* = `max(0, C - sum(a))`.

**Invariant linking bills and balance** (the main test oracle):

```
sum(remainingAmount of all bills) - unallocatedCredit = -(accountBalance + unbilledABRs)
```

Here `unbilledABRs` is the sum of ABRs with `customer_bill_id IS NULL`. In words: what the
customer owes on bills, minus credit not yet used, equals what they owe overall, minus
charges not yet billed.

Worked example (the mid-cycle case of `tests/test_billing_core.py:216`; cycle day 1,
300.00/month, product from Oct 15):

| Day | Event | Balance | Unbilled | Nov 1 bill (464.52) | Unallocated |
|---|---|---|---|---|---|
| Oct 15 | charge 164.52 | -164.52 | 164.52 | - | 0 |
| Oct 20 | payment 200.00 | 35.48 | 164.52 | - | 200.00 |
| Nov 1 | charge 300.00, bill 464.52 | -264.52 | 0 | `partiallyPaid`, remaining 264.52 | 0 |
| Nov 5 | payment 264.52 | 0.00 | 0 | `settled` | 0 |

Every row satisfies the invariant (for Oct 20: 0 - 200.00 = -(35.48 + 164.52)).

Two consequences:

- **The UI must show "due on bills" next to the balance.** On Oct 20 the balance is
  positive while 164.52 is already charged but not yet billed.
- **`creditLimit` is unaffected.** It stays a warning on the balance.

---

### Option A: derived settlement (computed on read, nothing stored)

`state`, `remainingAmount` and `appliedPayment` are **computed from the payments and
bills at read time**, with the arithmetic of 3.0. This follows the same approach as "No
balance column".

```mermaid
stateDiagram-v2
    [*] --> settled: issued with amountDue <= 0,\nor unallocated credit covers it
    [*] --> new: issued, no credit available
    [*] --> partiallyPaid: issued, credit covers part
    new --> partiallyPaid: payment (or negative bill) covers part
    new --> settled: payment covers the rest
    partiallyPaid --> settled: payment covers the rest
    settled --> [*]
```

A bill never moves backwards. Credits only grow, and a new bill always comes after the
existing ones in `bill_date` order, so it cannot take credit already used by an older
bill. This is a property to test.

- **Triggers:** none. The state follows from the data. A `POST /payment` shows up on the
  next read, immediately. The worker does nothing new apart from writing the due date
  below.
- **Allocation:** oldest first, implicit. `appliedPayment[]` is derived by overlapping
  intervals: payment j covers the credit interval `[C_(j-1), C_j)`, bill k the due
  interval `[cum_(k-1), cum_k)`, and the overlap is `appliedAmount`. Credits are ordered
  by `received_at` for payments and by `bill_date` for negative bills. Explicit allocation
  is **impossible** in this option.
- **Partial payments:** `partiallyPaid` with `remainingAmount`.
- **Overpayment / credit:** stays unallocated and settles the next bill when it is issued.
  That bill appears as `settled` (or `partiallyPaid`) from its first read.
- **Refunds (expected - charged):** refund ABRs land on the next bill and lower its
  `amountDue`. If the bill goes negative, its absolute value becomes credit for older open
  bills, or stays unallocated.
- **accountBalance:** unchanged.
- **API:**
  - TMF678 `customerBill` adds `remainingAmount`, `appliedPayment[]` (`PaymentRef` with
    `id`, `href`, `@referredType: Payment`), `paymentDueDate`, and optionally `billNo`
    (display id `BL-xxxxxxxx`), `runType: onCycle`, `category: creditNote` for negative
    bills.
  - `state` becomes derived. The `state=` filter must filter on the derived value (an SQL
    view with window sums, below). `lastUpdate` does not move when the derived state
    changes (only the row's `updated_at` would), which is a small deviation to document.
  - TMF676: no change to POST. `paymentItem` stays a 400. The response could optionally
    carry the derived `paymentItem[]`.
  - Internal API: none needed. The UI reads TMF678 directly, and "overdue" is computed in
    the UI from `paymentDueDate` and the context's business date.
- **UI:**
  - A **Bills** panel on the subscriber card: bill no., bill date, period, amount due,
    remaining, due date, state pill (reusing token colours), and "overdue" in red when
    past due.
  - The Billing account panel gets an "Outstanding on bills" KPI next to Balance.
  - Optional: a **Record payment** dialog (TMF676 POST), so the lifecycle can be
    demonstrated from the UI.
  - New `data-ui` markers mean new `scope.yaml` items, which need your approval.
- **Migration 0006:**
  - Add `payment_due_date date`, back-filled with `bill_date + N` and then set NOT NULL.
  - Either drop `customer_bill.state`, or keep it with its comment changed to "unused;
    derived". **Dropping is cleaner**: a stored `new` that is wrong would mislead anyone
    reading the table. The enum type can stay, because the lock test reads `pg_enum`.
  - Add a view, `customer_bill_settlement`: cumulative sums per account via window
    functions, giving `remaining` and `state` per bill.
- **Worker:** the adapter writes `payment_due_date` when it writes a bill
  (`app/billing/adapter.py:42-49` → `write_bill`). Nothing else.
- **Tests:**
  - A pure `app/billing/settlement.py` with hypothesis properties: states never regress
    when payments or bills are appended; `0 <= remaining <= amount`; at most one
    `partiallyPaid` bill per account, with all older bills settled; `sum(appliedAmount)
    = amountDue - remaining`; and the invariant of 3.0 against the ledger in
    `tests/billing_sim.py`.
  - A DB test that the SQL view equals the Python function on random data.
  - API contract tests (`tests/apiclient.py` validates the new fields against TMF678).
  - vitest with `expectUi`, and the e2e on seeded data (the seed's payments already give
    `settled` and `partiallyPaid` bills).
  - Negative proofs: allocate newest-first; ignore negative bills; count unbilled refunds
    as credit; settle `amountDue = 0` as `new`. Each must make a test fail.
- **Effort: S-M**, one slice. No new writer and no concurrency. The only migration is a
  column plus a view.
- **Limits:**
  - No explicit allocation and no `onHold`. Both break the "pure function of the data"
    property, so either one means moving to B.
  - No stored settlement time. It can be derived as the `received_at` of the payment that
    crossed `cum_k`.

### Option B: stored allocations, automatic oldest-first (TMF lettering)

A new table records every allocation. `customer_bill.state` is **stored and kept
current** in the same transaction as the allocation.

```mermaid
stateDiagram-v2
    [*] --> new: bill issued (worker)
    new --> settled: issued with amountDue <= 0 (same transaction)
    new --> partiallyPaid: allocation < remaining
    new --> settled: allocation = remaining
    partiallyPaid --> settled: allocation = remaining
    settled --> [*]
```

Allocations happen in `settle(conn, account_id, now)`, which takes the oldest unallocated
credit and puts it on the oldest open bill until one side runs out. It runs:

1. **in `POST /payment`**: lock the billing account `FOR UPDATE` (the same lock
   `bill_account` takes), insert the payment, settle;
2. **in `bill_account`**, after the bills are written (`apply_result`): a new bill uses
   up unallocated credit, and a negative bill adds credit;
3. **in every run, for every account.** That makes it self-repairing, and it back-fills
   all existing bills on the first run after the deploy. No data migration is needed.

- **Sub-variant B′, end-of-day settlement:** skip trigger 1. Only the run settles, using
  payments received before 00:00 of its run date, the same visibility rule as orders
  (`app/billing/adapter.py:21-25`). Replays are then fully identical, at the cost of bill
  states lagging up to one day behind a payment. **B (immediate) suits a demo better.**
- **Allocation:** oldest first, automatic. The per-bill totals equal option A's at all
  times; A's function becomes the test oracle for B.
  - *Optional slice B2, explicit lettering:* accept TMF676 `paymentItem[]` with
    `item = {id, @referredType: CustomerBill}` and `amount`. The 422 cases: a bill of
    another account, an amount above the bill's remaining amount, a sum above
    `totalAmount`. Whatever is not lettered goes oldest-first. A bill paid explicitly out
    of order makes "at most one `partiallyPaid`" false, so that property is dropped in B2.
- **Partial payments, overpayment and refunds:** as in A. Unallocated credit is
  `payments + negative bills - allocations`. When a negative bill is written, its credit
  is allocated like a payment's (allocation row with `credit_bill_id`).
- **accountBalance:** unchanged.
- **API:**
  - TMF678 as in A, but `state` is the stored column and `appliedPayment[]` comes from the
    table. `lastUpdate` moves when the state changes (`set_updated_at` trigger).
  - TMF676: optional derived `paymentItem[]` in the response, plus the B2 request above.
  - Internal API: optionally `unallocatedCredit` per account (or the UI computes it from
    the invariant).
- **UI:** as in A.
- **Migration 0006:**
  - Table `bill_allocation`: `id`, `billing_account_id`, `customer_bill_id`, `payment_id`
    or `credit_bill_id` (CHECK exactly one), `amount > 0 numeric(12,2)`, `currency`,
    `allocated_at` (injected clock), and composite FKs to bill, account and payment
    (adds `UNIQUE (id, billing_account_id)` on `payment`).
  - `payment_due_date` as in A.
  - "Allocated <= amountDue per bill" and "allocated <= amount per payment" cannot be a
    CHECK. They are enforced in `settle` and tested, or with a deferred constraint
    trigger if you want them in the database.
- **Worker:** `settle` inside `bill_account`'s transaction (after `apply_result`) and for
  every member account. It stays idempotent: nothing to allocate means nothing is
  written.
- **Rule change:** "only the billing run writes bills" becomes "only the billing run
  *creates* bills; settlement (the API and the run) changes their state". CLAUDE.md needs
  updating.
- **Tests:**
  - Everything listed for A, plus: the stored states equal A's pure function after any
    random sequence of payments and runs (hypothesis over `billing_sim`).
  - Concurrency, with two connections in the style of `tests/test_billing_db.py:168`: a
    payment and a run on the same account serialize on the account lock, and no credit is
    allocated twice.
  - Back-fill: bills issued before the migration settle on the first run.
  - B2: the 422 cases, and the lettering appears in both `appliedPayment` and
    `paymentItem`.
  - Negative proofs: no lock in `POST /payment`; allocation without the `FOR UPDATE`;
    newest-first.
- **Effort: M** (B), **+S** for B2. One new writer path in the API, one table, and one
  concurrency proof.

### Option C: full document lifecycle (B + validated/sent/onHold + due-date processing)

Option B, plus the TMF states that describe the bill as a document.

```mermaid
stateDiagram-v2
    [*] --> new: issued (worker)
    new --> validated: worker, same run (automatic check)
    validated --> sent: worker, same run (no channel: "made available")
    sent --> partiallyPaid: allocation < remaining
    sent --> settled: allocation = remaining, or amountDue <= 0
    partiallyPaid --> settled: allocation = remaining
    sent --> onHold: PATCH state=onHold (dispute)
    partiallyPaid --> onHold: PATCH state=onHold
    onHold --> sent: PATCH state=sent (released; nothing allocated yet)
    onHold --> partiallyPaid: PATCH state=sent (released; part allocated)
    settled --> [*]
```

- **Triggers:**
  - The worker moves new → validated → sent in the same run.
  - Allocation as in B.
  - **`PATCH /customerBill/{id}`** (`application/merge-patch+json`, `state` only, as
    `CustomerBill_MVO` allows) puts a bill on hold or releases it. Any other transition
    is a 409, following the pattern of `_check_lifecycle` in `app/api/catalog.py`.
- **Allocation:** oldest-first, **skipping `onHold` bills**. Credit that arrives while a
  bill is on hold goes to newer bills or stays unallocated. Releasing a bill triggers a
  `settle`.
- **Due date via the worker:** each run flags bills past `paymentDueDate` that still
  have money remaining. TMF has no overdue state, so this is an internal flag or a list,
  for dunning. Any *consequence*, such as suspending the account, depends on the open
  question "Suspension semantics" in CLAUDE.md and is **not** part of this option.
- **API:** as in B, plus TMF678 PATCH (it is 405 today:
  `tests/test_api_inventory_bill.py:180` would change) and possibly an internal
  `GET /bills?overdue=true`.
- **UI:** as in B, plus Put on hold / Release in a bill dialog, a "Disputed" pill, and an
  overdue filter on the subscribers list.
- **Migration:** as in B, plus nothing for states (the enum already has them).
- **Worker:** the issue-time transitions; the overdue pass.
- **Tests:** as in B, plus a transition table test (every pair of states x every
  trigger), on-hold skipping in allocation, and release re-settling. Negative proofs for
  each forbidden transition.
- **Effort: L**, 3-4 slices. `validated`/`sent` add states that carry no meaning here
  (there is no document and no channel), so they are mostly ceremony in this demo.

### Comparison

| | A derived | B stored allocation | C full lifecycle |
|---|---|---|---|
| States used | new, partiallyPaid, settled | same | all six |
| New writer | none | `POST /payment`, run | + PATCH, worker passes |
| Explicit allocation (`paymentItem`) | no | B2 | yes |
| Disputes (`onHold`) | no | no | yes |
| Replay-identical | yes | B′ only | B′ only |
| Concurrency risk | none | payment vs run (lock) | same |
| Migration | column + view (+ drop `state`) | table + column | same as B |
| Effort | S-M | M (+S for B2) | L |

---

## 4. Edge cases

| Case | A | B | C |
|---|---|---|---|
| **Payment before the first bill** | Unallocated credit. The first bill reads `settled` or `partiallyPaid` from its first read. | Payment stored without allocation. The run that issues the first bill allocates it in the same transaction, so the bill is never visible as `new` if credit covers it. | As B. The bill goes new → validated → sent → settled in one run. |
| **Payment larger than all open bills** | Open bills `settled`; the rest is unallocated credit (balance positive) and settles later bills. No payout: a TMF676 `/refund` is out of scope. | Same. The payment has allocations below its amount; the remainder is used by later bills. | Same. |
| **Bill with zero amount** (`tests/test_billing_core.py:232`) | `settled` at issue, `remainingAmount` 0, no `appliedPayment`. | Written `new` then set to `settled` in the same transaction (or inserted as `settled` directly). | `settled` directly after sent. |
| **Refund producing a negative bill** (`tests/test_billing_core.py:223`) | `settled`, remaining 0, `amountDue` stays negative; optionally `category: creditNote`. Its absolute value is credit for older open bills, then for later ones. | Same; allocation rows carry `credit_bill_id`. | Same. A negative bill cannot be put on hold (409). |
| **Bill issued during catch-up for an old date** | `billDate` is in the past. If `paymentDueDate = billDate + N` and the catch-up is more than N days late, the bill is overdue the moment it appears. All credit known at read time applies, including payments received after `billDate`. Final per-bill totals are order-independent. **Pairings** (which payment paid which bill) can shift when a negative bill for an old date arrives after newer payments. | Same totals; settled with all payments known when the catch-up run executes (B) or only those before 00:00 of the replayed date (B′, identical to the daily history). | As B. The overdue pass must not dun a bill whose due date passed before it existed. |
| **Payment and run at the same moment** | No writes, no race. | Both lock the account row. The second waits and then sees the first's allocations. | As B. |
| **A missing price makes the account fail in a run** | Not affected. | The run's `settle` for that account is rolled back with the account (`BillingError`); `POST /payment` still settles. | As B. |

On the due date for catch-up bills, two choices:

- **(i) `billDate + N`.** Deterministic and replay-identical, but a bill issued very late
  is overdue at once.
- **(ii) `max(billDate, issue date) + N`.** The issue date is the business date of the
  issuing run's attempt, which uses the injected clock (`billing_run.started_at` is reset
  on a reopen or takeover, so it must be captured at write time). Fairer to the customer,
  but the replay test then has to exclude `payment_due_date`.

---

## 5. Recommendation and open questions

**Recommendation: option A now, built so that B can replace it without visible change.**

- It fixes the visible problem (bills progress; `remainingAmount`, `appliedPayment`,
  `paymentDueDate` are exposed) in one slice, with no new writer and no concurrency.
- It follows the project's existing approach: computed rather than stored (as the
  balance), replay-identical, pure function plus property tests.
- Its pure settlement function is exactly the oracle B needs. If explicit lettering
  (B2) or disputes (C) ever matter, B swaps the storage, and the API output stays
  identical by test.
- C's `validated`/`sent` describe a bill document and a delivery channel that do not
  exist here. They would be states without meaning.

Proposed order for A:

1. Pure `settlement.py` with properties and negative proofs.
2. Migration 0006 (due date, view, `state` column decision).
3. TMF678 fields and the derived `state` filter.
4. The UI Bills panel and the "Outstanding on bills" KPI (plus Record payment if you
   want it).
5. `scope.yaml` items (your approval), `make progress`, CLAUDE.md.

**Questions for you:**

1. **Option:** A, B (with or without B2, B vs B′), or C?
2. **Payment term N** (days from bill date to `paymentDueDate`): 14? 10? And for catch-up
   bills, (i) `billDate + N` or (ii) `max(billDate, issue date) + N`?
3. **Stored `state` column** (A only): drop it, or keep it as an unused column with a
   changed comment?
4. **Negative bills:** credit for *older* open bills as well (proposed), or only for
   later ones? Mark them `category: creditNote`?
5. **Overdue:** only a UI flag (A), or also a worker pass (C)? Should it ever have a
   consequence? That ties to the open question "Suspension semantics".
6. **Paying out credit** (TMF676 `/refund`): out of scope for now? (Proposed: yes.)
7. **Record payment in the UI:** add the dialog in this work, or keep payments API-only?
8. **Extra TMF678 fields:** besides `remainingAmount`, `paymentDueDate` and
   `appliedPayment`, do you want `billNo`, `runType`, `category`?
9. **Derived `paymentItem[]` in TMF676 responses:** yes or no (A and B)?
10. **`scope.yaml`:** new api/ui items (the Bills panel, the Record payment dialog if
    chosen), which need your approval when the slice is built.

---

## 6. Decisions and the design to build (option C)

### 6.1 Decisions (2026-10-10)

Your answers were numbered by the short list in the chat reply, not by the ten questions
above. Mapped to section 5:

| # | Question | Decision |
|---|---|---|
| 1 | Option | **C** (overrides the recommendation of A). |
| 2 | Payment term | **30 days, configurable**: `BILL_PAYMENT_TERM_DAYS`, default 30. |
| 2b | Due date of a catch-up bill | *Not answered; my choice:* **`billDate + N`** (rule (i)). It is deterministic, so the replay test stays exact, and with N = 30 a bill is only overdue on arrival after a catch-up more than 30 days late. |
| 3 | Drop the `state` column | n/a: option C stores the state. |
| 4 | Negative bills credit older open bills | **Yes.** |
| 5 | Overdue | **No consequence for now**: no worker pass and no dunning. `paymentDueDate` is exposed, and the UI marks a bill as overdue (display only). |
| 6 | TMF676 `/refund` (paying credit out) | *Not answered; my choice:* **out of scope**. |
| 7 | Record payment in the UI | **Yes.** |
| 8 | Extra TMF678 fields | My preference (below): `billNo`, `runType`, `category`. No tax fields: taxes are out of scope, and a `taxIncludedAmount` would claim something we do not compute. |
| 9 | Derived `paymentItem[]` in TMF676 responses | *Not answered; my choice:* **yes**. It is the payment-side view of the same allocations, and the request side carries the explicit lettering. |
| 10 | `scope.yaml` | The items are listed in 6.8; each change still needs your approval when its slice is built. |

**Also decided here: settlement is immediate.** I chose B, not B′: `POST /payment` settles
in its own transaction, so the operator sees the effect at once. Replays of billing runs
stay identical because the replay test records no payments, and the property test below
proves per-bill totals do not depend on when settlement runs.

### 6.2 States and transitions

```mermaid
stateDiagram-v2
    [*] --> new: billing run writes the bill
    new --> validated: same run, validation check passes
    validated --> sent: same run ("made available"; no delivery channel)
    sent --> settled: amountDue <= 0 (same run)
    sent --> partiallyPaid: allocation < remaining
    sent --> settled: allocation = remaining
    partiallyPaid --> settled: allocation = remaining
    sent --> onHold: PATCH state=onHold
    partiallyPaid --> onHold: PATCH state=onHold
    onHold --> sent: PATCH state=sent (nothing allocated)
    onHold --> partiallyPaid: PATCH state=partiallyPaid (part allocated)
    settled --> [*]
```

- **new → validated → sent happen inside the issuing transaction** (`bill_account`), so
  no reader ever sees `new` or `validated` on a fresh bill. They are kept as real states
  for two reasons: the state history (6.3) records each step, and the validation is a
  real check. *Validated* means three things: `amount_due` equals the sum of the ABRs
  attached to the bill; every ABR on it is the account's and in its currency; and the
  period is `[billDate, next cycle day)`. A failure raises `InconsistentState`, so the
  account fails in the run and nothing is written. That is the same handling as every
  other billing inconsistency.
- **Bills already `new` in a database** (production has had them since 2026-10-05) take
  the same path. Every run applies the issue step to any bill of the account still in
  `new`, so the first run after the deploy validates, sends and settles them. There is
  no data migration for states.
- **`settled` is final.** Allocations are append-only, like payments, so a bill never
  goes back. A dispute of a paid bill is out of scope (409).
- **onHold / release** come only from `PATCH /customerBill/{id}`. The PATCH sets exactly
  the requested state, or answers 409 with the reason; a request is never silently
  rewritten into another state. To release, the client sends the state that matches the
  allocations: `sent` if nothing is allocated, `partiallyPaid` otherwise. Anything else
  is a 409 naming the right one. Every other transition by PATCH is a 409, in the
  pattern of `_check_lifecycle` (`app/api/catalog.py:62`). Repeating the current state
  is a 200 no-op. After a release, settlement runs (credit may have been waiting).
- **An `onHold` bill is skipped by automatic allocation.** Credit goes to newer open bills
  or stays unallocated. An explicit allocation to an `onHold` bill is a 409.

### 6.3 Data model (migration 0006)

- `customer_bill`:
  - **`payment_due_date date NOT NULL`**: back-filled `bill_date + 30` in the migration
    (a literal, since migrations are immutable), then written at issue as
    `bill_date + BILL_PAYMENT_TERM_DAYS` in force at that moment. A later config change
    does not move existing due dates.
  - **`category`**: an enum, ours: `normal` | `creditNote`. `creditNote` iff
    `amount_due < 0`, enforced by a CHECK.
  - The TMF fields `billNo` and `runType` are derived and need no columns. `billNo` is
    `BL-` plus the first 8 hex digits of the id in upper case, like `BA-xxxxxxxx`.
    `runType` is always `onCycle`.
- **`bill_allocation`**, ours: one row per amount applied to a bill.
  - Columns: `id`, `billing_account_id`, `currency`, `customer_bill_id` (the bill paid);
    exactly one source, `payment_id` or `credit_bill_id` (a negative bill;
    `num_nonnulls(...) = 1`); `amount numeric(12,2) > 0`; `kind` (`automatic` |
    `explicit`); `allocated_at` (the injected clock).
  - Composite FKs keep everything in one account and currency:
    `(customer_bill_id, billing_account_id)` and `(credit_bill_id, billing_account_id)`
    to `customer_bill`; `(payment_id, billing_account_id)` to `payment`, which needs a
    new `UNIQUE (id, billing_account_id)`; `(billing_account_id, currency)` to
    `billing_account`. Also `credit_bill_id <> customer_bill_id`.
  - Append-only: no UPDATE or DELETE from the app.
- **The amount bounds are a constraint trigger**, `DEFERRABLE INITIALLY DEFERRED`, so the
  database enforces them:
  - per paid bill: total allocated <= `amount_due`;
  - per payment: total allocated <= `amount`;
  - per credit bill: total allocated <= `-amount_due`;
  - the paid bill is positive and the credit bill negative.

  A CHECK cannot do this because it spans rows. Settlement code stays within the bounds
  anyway; the trigger is the backstop, with a negative proof (an over-allocation written
  by hand fails at commit).
- **`customer_bill_state_change`**, ours: `customer_bill_id`, `from_state` (NULL on
  issue), `to_state`, `changed_at` (the injected clock), `cause` (`run` | `payment` |
  `operator`), and `operator` (the `X-Remote-User` value for a PATCH; display only, see
  "Trust in X-Remote-User"). It is the state history in the bill dialog, and it gives
  TMF's state-change semantics without implementing `/hub`.
- `remainingAmount` is not stored: it is `amount_due - sum(allocations to the bill)`,
  0 for `amount_due <= 0`. The stored `state` must agree with it. The property tests and
  a DB test check this (`settled` ⇔ remaining 0; `sent` ⇔ nothing allocated; and so on).
- `lastUpdate`: every allocation to a bill also touches its row, so `updated_at` (the
  `set_updated_at` trigger) moves even when the state stays `partiallyPaid`.
- Comments for every new table and column, as in `0005_schema_comments.py`.

### 6.4 Settlement (one pure function, three callers)

- **`app/billing/settlement.py`** is pure, like `core.py`. Its input is the account's
  open bills (positive, not `onHold`, with remaining amounts, in `bill_date` order), its
  unused credits (payments in `received_at` order, then negative bills in `bill_date`
  order, each with its unused amount), and any explicit items. It returns the
  allocations and the new state of each bill.
- **Explicit items are placed first**, then **oldest credit to oldest open bill** until
  one side runs out.
- **Callers**, each holding the account lock (`billing_account ... FOR UPDATE`, the lock
  `bill_account` already takes at `app/repository/billing.py:570`):
  1. `POST /payment`: lock, insert the payment, settle (explicit items first).
  2. `bill_account`: after `apply_result`, issue the new bills (6.2), then settle. This
     runs in every run for every member account, so it also back-fills and repairs. It
     is idempotent: with nothing to allocate, nothing is written.
  3. `PATCH /customerBill/{id}` release: lock, change the state, settle.
- **The core is unchanged.** `compute()` still returns ABRs and bills only.
  `apply_result` writes `payment_due_date` and `category` with the bill.
- **CLAUDE.md rule update:** "only the billing run writes ABRs and bills" becomes "only
  the billing run *creates* bills; settlement (payments, the run, release) and the
  operator's hold/release change their state and allocations".

### 6.5 API

TMF678 `customerBill`:

- New in GET responses: `remainingAmount`, `paymentDueDate` (midnight in BILLING_TZ, like
  `billDate`), `appliedPayment[]`, `billNo`, `runType: onCycle`, `category`.
  - `appliedPayment[]` is `{appliedAmount, payment: PaymentRef}`, one entry per payment,
    summing its allocations.
  - A credit-bill source is not a payment, so it is **not** listed in `appliedPayment`.
    It shows up as the difference `amountDue - remainingAmount - sum(appliedPayment)`.
    The internal bill detail (below) lists both kinds of source.
- `PATCH /customerBill/{id}`: `application/merge-patch+json`; `state` only; plus
  `@type` as everywhere. Any other `CustomerBill_MVO` attribute (`billCycle`) is a 400,
  as is any other attribute. The rules are in 6.2.
- Filters: `state` stays (the stored column). A new filter, **`bill.id`**, on
  `appliedCustomerBillingRate`, for the bill dialog.

TMF676 `payment`:

- POST accepts **`paymentItem[]`**: `{item: {id, @referredType: "CustomerBill"}, amount}`
  (an optional `totalAmount` must equal `amount`; an optional `taxAmount` must be 0).
- 422 (no reference to a stored resource conflicts): a bill that does not exist or
  belongs to another account; a `@referredType` other than `CustomerBill`; a duplicate
  bill; an amount <= 0, with more than 2 decimals, or not in the payment's currency;
  items summing to more than `totalAmount`.
- 409 (conflicts with stored state): an amount above the bill's remaining amount, or a
  bill that is `onHold` or `settled`.
- Whatever is not lettered is allocated oldest-first.
- GET responses carry **`paymentItem[]`**: every allocation from this payment, explicit
  and automatic, summed per bill. It **grows over time**: a payment's unused credit is
  allocated when later bills are issued. This is documented in the spec deviation notes.
- Unchanged: `status` is always `done`, PATCH/DELETE give 405, and `/refund` is not
  exposed.

Internal API (`spec/internal/bss-internal.oas.yaml`):

- **`GET /billingAccount/{id}/settlement`**: `{outstanding, unallocatedCredit, overdue
  {count, amount}, onHold {count, amount}, paymentTermDays}`. `outstanding` is the sum
  of `remainingAmount` over bills that are not settled, `onHold` included. Overdue means
  `paymentDueDate < businessDate` and remaining > 0. It powers the KPIs, since
  `unallocatedCredit` cannot be computed from TMF lists without re-implementing
  settlement.
- **`GET /customerBill/{id}`** (internal detail): the bill with its ABRs, its sources
  (payments and credit bills, with amounts and kinds), the state history, and
  `allowedStates` (what PATCH accepts now), so the UI never re-implements 6.2.
- `GET /context` adds `paymentTermDays`.

Invariant tested through the API, per account:

```
sum(remainingAmount) - unallocatedCredit = -(accountBalance + unbilled ABRs)
```

It holds without holds and lettering. Holds and lettering change *which* bills are open,
not the totals, so it holds always.

Spec deviations to add to CLAUDE.md:

- `paymentItem` is computed and grows over time.
- A credit-bill source is not in `appliedPayment`.
- The PATCH transition rules.
- `validated`/`sent` are set automatically in the same run.

### 6.6 Configuration

- `BILL_PAYMENT_TERM_DAYS` in `app/config.py`: an integer, 1..365, default 30. An
  invalid value fails settings validation at startup, so neither the app nor the worker
  starts (as built in slice 1: a pydantic `Field(ge=1, le=365)`, the same mechanism as
  `BILLING_RUN_AT`'s pattern).
- Only the worker uses it to write due dates. The app reads it for `/context`.
- Like `BILLING_TZ`/`BILLING_RUN_AT`, it is **passed to neither service** today, so both
  use the default. Changing it on the VPS means adding it to the `environment:` of both
  `app` and `worker` in `compose.prod.yaml`, a compose change that needs your OK.

### 6.7 UI

- **Subscriber card, Billing account panel:** new KPIs "Outstanding on bills" (with
  "N overdue" in red, and "N on hold") and "Unallocated credit", from the settlement
  endpoint.
- **Bills panel** (between Products and Charge history): bill no., bill date, period,
  amount due, remaining, due date, state pill (+ an "Overdue" caption), through
  `ListState`. The pills reuse existing token colours only (decision under "Frontend").
- **Bill dialog** (row click, `Modal ui="bill-detail"`): the ABRs on the bill, its
  payments and credit notes, the state history, and **Put on hold** / **Release** with
  an inline confirm, offered only when `allowedStates` permits.
- **Record payment dialog** (a button on the card):
  - Amount (sent as an exact JSON number token, like `jsonWithAmount`), and a method
    from a short list (`Cash`, `bankTransfer`, `card`), sent as the `name`.
  - Optionally **Apply to bills**: the open bills (not on hold) with amount fields,
    prefilled oldest-first up to the payment amount and editable. Left untouched, it
    sends no `paymentItem` and the server allocates oldest-first.
  - After the POST, the card reloads.
- Writes are pinned on both sides as usual: component tests assert the exact bodies of
  the payment POST and the bill PATCH, and `tests/test_ui_requests.py` replays them
  against the real API.
- **Seed:** besides the existing payments (which will produce `settled` and
  `partiallyPaid` bills automatically), one `onHold` bill and one lettered payment, so
  every state and both allocation kinds show up locally. The e2e stays read-only and
  data-tolerant.

### 6.8 Progress tracker items (each needs your approval when its slice lands)

- **api:**
  - `PATCH /tmf-api/customerBillManagement/v5/customerBill/{id}`
  - `GET /internal/v1/billingAccount/{id}/settlement`
  - `GET /internal/v1/customerBill/{id}`
- **ui:** `bills` (the panel), `bill-detail` (the dialog), `record-payment` (the
  dialog), `bill-hold` (the hold/release action).
- **db:** `bill_allocation`, `customer_bill_state_change`.
- **worker:** `bill-issue` (new → validated → sent with the check) and `bill-settle`
  (settlement in the run), proven by `@pytest.mark.capability` tests.

### 6.9 Tests and negative proofs

- **Pure settlement (hypothesis):**
  - allocations stay within all bounds;
  - oldest-first without lettering;
  - `onHold` bills are never allocated automatically;
  - explicit items are honoured exactly;
  - states never regress except by PATCH;
  - stored state agrees with remaining;
  - **without holds or lettering, per-bill remaining equals the cumulative formula of
    3.0** (the option A function, kept as the oracle);
  - per-bill totals do not depend on the interleaving of payments and runs (daily vs
    catch-up, settled at payment time vs only in runs).
- **DB:**
  - the constraint trigger (over-allocation by hand fails at commit);
  - composite FKs (another account's payment or bill);
  - **concurrency**, two connections in the style of `tests/test_billing_db.py:168`:
    payment vs run, payment vs payment, release vs payment, all serialized on the
    account lock with no credit allocated twice (negative proof: drop the lock in
    `POST /payment` and the test fails);
  - back-fill (bills inserted `new` are issued and settled by the next run);
  - the replay test (`tests/test_billing_db.py:285`) still compares equal, due dates
    included.
- **API:**
  - contract validation of every new field (`tests/apiclient.py`);
  - every PATCH transition, allowed and forbidden;
  - the 422/409 table of lettering;
  - `paymentItem` growing after a later bill;
  - the invariant of 6.5 after a scripted history.
- **Worker:** the validation check (a bill whose ABR sum was changed by hand fails the
  account, `failed`, nothing written); due date from the config; capability tests for
  `bill-issue` and `bill-settle`.
- **UI:** vitest with `expectUi` for the four markers; both time zones (the suite runs
  in UTC and New York); the request bodies.
- **Negative proofs, each made once with Edit and reverted:** newest-first allocation;
  holds not skipped; validation skipped; a negative bill not used as credit; PATCH
  accepting `sent` with allocations present; `paymentItem` above remaining accepted.

### 6.10 Slices

Each slice follows the usual routine: plan, tests, negative proofs, `make lint`,
`make test` x3, `make progress`, CLAUDE.md, then the diff and a commit only after your OK.

| Slice | Content | Size |
|---|---|---|
| **1. Schema + settlement core** | migration 0006 (6.3), `settlement.py` with properties, repository functions, `BILL_PAYMENT_TERM_DAYS` | M |
| **2. Worker** | issue step with validation, settlement in `bill_account`, due date and category at write, back-fill, concurrency tests, capabilities | M |
| **3. API** | TMF678 fields + PATCH + `bill.id` filter; TMF676 `paymentItem` (request and response) with the account lock; internal settlement + bill detail + context; spec file | M-L |
| **4. UI + seed** | KPIs, Bills panel, bill dialog with hold/release, Record payment dialog, seed additions, `test_ui_requests.py`, e2e | M-L |

Slices 1-2 change nothing visible except that bills leave `new` (`sent`, then
`partiallyPaid`/`settled`) from the first run after deploy. That is the moment
production's existing bills catch up. Slices 3-4 then expose it.

### 6.11 Still open (small; defaults stated, change any)

- **Release by PATCH must name the matching state** (`sent` or `partiallyPaid`), or it is
  a 409. The alternative is to accept `sent` and store whatever the allocations imply.
  Default: strict.
- **Overdue bills count in "Outstanding"**, and so do bills on hold (shown separately as
  "N on hold"). Default: yes.
- **Payment methods offered in the UI:** `Cash`, `bankTransfer`, `card`. The seed uses
  "Faster Payments" too. Default: those three plus a free-text "Other".
