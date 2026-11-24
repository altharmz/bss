# Bill lifecycle (option C)

The approved refund/bill-delivery milestone extends credit sources with payout
consumption, adds worker-owned off-cycle requests and immutable issued JSON bill
documents. See [billing-completion.md](billing-completion.md). Its rules supersede
the original payment-only source totals where payouts are present.

Index: [README.md](README.md).

## Bill lifecycle (option C, in progress)

Design and decisions: **`docs/archive/bill-lifecycle-design.md`**, section 6 (decided
2026-10-10: option C, payment term 30 days configurable, credit notes pay older open
bills, overdue is display only, a Record payment dialog). **Overdue is no longer display only
since #385:** an overdue postpaid bill starts dunning (reminders, suspension, restoration when
paid; held bills never count), [dunning.md](dunning.md). Four slices: 1. schema +
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
["Internal API"](internal-api.md#internal-api-stage-5)). `tests/billing/test_api_bill_settlement.py`; negative proofs: no settlement in
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
- Proven (`tests/billing/test_bill_lifecycle_db.py`): issue steps and idempotence; the stage 3
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
- **Tests**: `tests/billing/test_settlement.py` (examples, hold/release table, hypothesis: the
  remaining per bill equals the cumulative rule of the design's 3.0 whether payments
  settle at once or only in runs; no open bill and unused credit at once; holds skipped;
  explicit items exact), `tests/billing/test_settlement_db.py` (round trip, trigger bounds at
  commit, FKs, CHECKs), `test_0006_on_existing_bills`. Negative proofs, each made once
  and reverted: newest-first allocation, holds not skipped, credit notes ignored,
  release to any open state, the trigger's bill bound doubled, the due date ignoring the
  configured term - each failed at least one test.

## UI: global bills, bill cycles and rates (slice 20, 2026-10-25)

Built as designed, frontend only.
`screens/GlobalBills.tsx`, `screens/BillLinks.tsx` and `api/bills.ts`: the `/bills` screen
(Billing > Bills) with Bills | Bill cycles tabs, a state filter (server), the `?account=` chip (sent
as `billingAccount.id`) and a billing date (client side); a bill row opens the existing bill dialog
(hold/release PATCH included), which now ends with **Related**: a **Bill cycle** button (when the bill
has a `billCycle` ref) and **Rates** (`GET appliedCustomerBillingRate?bill.id=`, period, product,
amount, tax; a row opens the raw GET). Both load only when opened. The card's bill dialog gets the same.
Tests: `GlobalBills.test.tsx` and `tests/internal/test_ui_requests.py::test_global_bill_cycle_and_rate_reads_as_the_ui_makes_them`.
Deviations: the API has no `billingDate` filter on bills, so the date filter compares `billDate` on
the loaded rows (bill cycles likewise, on their `billingDate`); a bill cycle is a daily run and has no
account, so the cycles tab has name, billing date and payment due instead of an account column;
"kind" is the bill's `runType`. TEST-CHANGE: the shell test's expected sidebar links gain `/bills`.

## PDF documents and email delivery (#202, 2026-11-18)

Slice 1 of postpaid for the MVNO (epic #182); dunning is #385, the UI #386. Decided in the issue
(2026-11-15, the human's delegation). Migration `0086`.

- **A PDF next to the JSON snapshot, not instead of it.** `billDocument` lists the inline JSON
  attachment first, then the PDF as a TMF678 `AttachmentRefOrValue` with `url`
  `/tmf-api/customerBillManagement/v5/customerBill/{id}/document.pdf`, `mimeType`
  `application/pdf`, `size` in bytes and `id` `{bill id}-pdf`. The gap
  `missing billDocument.url` is closed (`tests/tmf-gaps.txt`).
- **Rendering:** `app/billing/bill_pdf.py` (pure, `reportlab` 5.1.0, BSD; its `pillow` and
  `charset-normalizer` are pinned in `constraints.txt`) with the vendored DejaVu Sans 2.37 regular
  and bold (`app/billing/fonts/`, licence beside them; the release tarball's SHA-256
  `fa9ca4d1...e17d7`), so any name prints. Layout: issuer (`BSS_BILL_ISSUER_NAME`, default "BSS
  Mobile"; `BSS_BILL_ISSUER_ADDRESS`, lines separated by `;` or newlines), bill number, bill date,
  billing period (last day printed), payment due date, account name and id, the customer's name
  and billing address (the account's `contact`: the first postal medium of a `billingAddress`
  contact, else of any contact), the charges (format `JSON detailed`) or none (`JSON summary`),
  the totals with tax per category and rate, the payments and credit notes applied, and the amount
  still due by the due date (a credit note says there is nothing to pay). Money is formatted from
  `Decimal` (`money` refuses anything else). `invariant=1`: the same data gives the same bytes.
- **When:** in the account's billing transaction, **after** the run settles it
  (`adapter.store_documents`, called by `bill_account` and the off-cycle `_fulfill` with the ids
  `issue_bills` now returns), so the PDF shows what paid the bill at issue. The format is read
  from the bill's JSON snapshot, written earlier in the same transaction. A render failure is
  `DocumentFailed` (a `BillingError`): the account fails in the run with nothing of it written,
  like any inconsistency; an off-cycle request ends `terminatedWithError`. Prepaid postings
  (which snapshot JSON on their own path) get no PDF: the issue is about postpaid bills.
- **Storage:** `customer_bill_document` (bill id PK/FK, `pdf` bytea, `sha256`, `rendered_at` =
  the attempt's `started_at`), immutable by trigger (UPDATE and DELETE raise). Bills issued
  before 0086 have none (no invented history): no PDF attachment, and their URL is a 404.
- **Serving:** `GET .../customerBill/{id}/document.pdf` streams it (`Content-Type:
  application/pdf`, `inline` with `BL-xxxxxxxx.pdf`) behind the same authentication as every
  route; 404 for an unknown bill or one without a PDF. TMF678 defines the url, not this
  operation, so the generated docs list it under `NO_SPEC`. Not an MCP tool (scope excluded:
  the connector answers JSON, and `get_customer_bill` carries the bill and its JSON document).
- **Dependencies:** besides the new pins, the transitive `opentelemetry-api` moved 1.45.1 ->
  1.45.2 (CVE-2026-126360, published after master's last green run; the PR's pip-audit gate
  refused it), by the procedure of [infra.md](infra.md) "Dependencies are pinned": both images
  resolve exactly to the constraints, pip check clean.
- **Cost:** measured 13 ms a bill (a five-charge detailed bill, 1000 renders in the dev
  container, alone; 21 ms of CPU with other stacks busy), about 45 KB a PDF with the subset
  font. `tests/billing/test_bill_pdf.py::test_rendering_takes_under_50_ms_a_bill` holds the 50 ms target
  when run alone; under `-n auto` (the hook, `make test`) the same renders took 79 ms on the wall
  clock and 154 ms of CPU, so there it fails only above 1 s a bill (a timing gate would be flaky,
  like the worker heartbeat test). The bill list reads the PDF size in its
  page query (`billing.PDF_SIZE`), so a page keeps its statement count.
- **Delivery:** a presentation medium named `email` (any case) on the account means *email the
  bill*; other media stay descriptive, postal mail is out of scope. The notification worker's
  detector `bills_issued` (`app/notify/events.py`) finds bills whose PDF was rendered within the
  lookback (so only bills issued since 0086) on such accounts and queues event `bill_issued`
  (dedupe key `bill_issued:{bill id}`, transactional, **email only**: no SMS, not even to the
  line) to the customer's preferred email (notifications.md recipient rules); no email address
  gives one cancelled `noRecipient` Email row. Subject "Your bill BL-xxxxxxxx", a body with the
  period, the bill total, the amount due and the due date, and the PDF attached
  (`notification_message.customer_bill_id`; a resend carries it again). The medium is read when
  the detector runs. **`BSS_BILL_EMAIL_ENABLED`** (default true) switches the detector off; the
  three settings are not passed in `compose.prod.yaml`, so production runs the defaults until the
  human sets them.
- **Adapter:** `send_email(key, to, subject, body, attachments=())`, each attachment
  `(filename, mime_type, bytes)`; the `log` sender records them (and logs names and sizes, not
  content). A bill email whose PDF is missing is not sent without it: the attempt fails and
  retries. The real provider is #203.
- **TMF681:** a bill email lists the PDF in `attachment` (by `url`, `attachmentType` bill); an
  operator's POST with `attachment` is still a 422.
  **#386:** the message list filters on `attachment.id` (`{bill id}-pdf`), and the internal bill
  detail carries `pdf` (url and size, or null): the bill dialog's PDF link and delivery line.
- **Tests:** `tests/billing/test_bill_pdf.py` (text read back with `pypdf` 7.1.1, dev only: bill number,
  total, amount due after a payment applied at issue, every charge name, the due date; summary vs
  detailed; not rewritten by a rename or the next run; immutability trigger; render failure fails
  only that account with no bill or ABR written; off-cycle bill; legacy bill and unknown bill
  404; 401 without a session; one email with the PDF even when the worker runs twice, none
  without the medium, none with the switch off, any-case medium name, no address cancelled and
  not texted, resend carries the PDF; render time). Negative proofs, each made once and
  reverted: the switch check removed (the switch test failed), the PDF rendered before the
  settlement (the payment-applied assertion failed). TEST-CHANGE: the bill's `billDocument` has
  two entries (`test_api_document_attributes.py`), the template test knows the bill parameters, the table
  count is 97 (`test_migrations.py`, `test_nonsuperuser_migration.py`), the url gap line is
  removed.
