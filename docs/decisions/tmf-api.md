# TMF API: conventions and mappings (TMF632, 629, 666, 676, 637, 678, 622)

The approved [refund/bill-delivery milestone](billing-completion.md) adds TMF676
refunds, TMF678 customerBillOnDemand, and TMF666 billFormat/billPresentationMedia.
It extends account billStructure with writable format/media preferences while
keeping cycleSpecification read-only. Cash payouts reduce balance and unused
credit; issued bills carry immutable JSON attachments. These decisions extend
the original mappings below. TMF operation coverage becomes 67/129 (51.9%).

Decision records moved from CLAUDE.md (as of `910ec0a`), unchanged in meaning. The rules
needed every session stay in [CLAUDE.md](../../CLAUDE.md); `scripts/check-decisions.py`
proves nothing was lost in the move. Index: [README.md](README.md). The TMF620 catalog
mapping is in [catalog.md](catalog.md).

## TMF API conventions (stage 2)

### Billing cycle specification discovery (2026-10-14)

TMF666 now exposes read-only `GET /billingCycleSpecification` and
`GET /billingCycleSpecification/{id}`. The single supported specification has the
stable id `monthly`, `billingPeriod` and `frequency` equal to `monthly`, and
`billingDateShift: 0`: bills collect unbilled charges at the account period start.
This is the existing billing policy, available even when no accounts exist, not a
new configurable schedule. Catch-up runs retain the original business dates.
Offsets for mailing, payment due dates, credits and charges are omitted rather than
invented: these cannot all be expressed as fixed offsets from the period start.

Every billing account returns a read-only `billStructure.cycleSpecification`
reference to this policy. The account's `billCycleDay` extension still sets its
calendar anchor (1..28); it is not a `billingDateShift`. POST/PATCH reject
`billStructure` rather than silently accepting an unsupported policy choice.
The list supports equality filters on name, billingPeriod and frequency, standard
paging/count headers and fields selection; retrieval accepts fields only. Other
ids return 404. No migration or billing behavior change is needed. The two new
operations also have read-only MCP tools and vendored-spec contract tests in
`tests/test_api_cycle_specification.py`.

Local validation: 24 focused contract/MCP tests and three full backend runs passed
(882 tests each; two dev-profile runs and one CI-profile run). Backend/frontend lint
and 226 UI tests in each configured time zone passed, as did decisions-check.
Changing billingDateShift to 1 made the policy test fail; it was restored to 0.
`make docs` regenerated the API inventory, whose path/method comparison with the
eight vendored specs confirms 45/129 non-listener operations implemented.
The human applied the two API scope entries together with the policy change making
`docs/progress/scope.yaml` editable within authorized feature work. No commit or
deployment was made for this slice.
`make progress` then passed all 882 backend and 226 UI tests and regenerated the
report: db 18/18, API 61/61, UI 19/19 and worker 12/12 implemented and tested.

### Input correctness and read completeness (2026-10-14)

One slice covers gap-analysis C1 (`@type`), C2 (comma filters), C6 (write queries),
B1 (prices), B2 (navigation) and B3 (bill cycles). The rules below extend the original
stage-2 mappings; they do not add taxes, payment processing or order modifications.

- Every TMF POST checks the supplied root `@type`, as PATCH already did. Missing
  `@type` remains accepted for existing clients. A foreign or null value is a 400
  before any write. Billing-account creation accepts `BillingAccount` and
  `BssBillingAccount`; order items, when typed, must be `ProductOrderItem`.
- TMF POST and PATCH accept only `fields`, exactly once. It selects the response's
  top-level attributes, retaining `id`, `href`, `@type` and POST's `Location`.
  Unknown query parameters or fields are a 400 before the transaction opens.
  This does not change which request attributes are writable. MCP write tools expose
  the same optional `fields` argument.
- Equality filters on statuses/types, UUIDs, booleans and dates reject comma-separated
  values with 400; there is no OR filter syntax. Commas in free-text names remain
  literal. Repeated paging and fields parameters are errors rather than last-value-wins.
- `Customer.account[]` returns all of the customer's billing-account references,
  including accounts beyond the default page size. References use `AccountRef` with
  `@referredType: BillingAccount`. Product reads expose `productSpecification`, the
  owning Customer as `relatedParty` (`role: customer`, `PartyRoleRef`), and `orderDate`
  equal to the creating order's injected-clock `creationDate`.
- `Product.productPrice[]` is the full monthly recurring fee applicable to the
  current billing period: price selection is at max(product start, period start),
  matching the billing core. A mid-period price change takes effect at the next
  period. Future products use their start date; ended products use their last
  billable period; cancelled/aborted products have an empty price list. Missing prices
  yield an empty list, not an invented amount. `ProductPrice` contains the catalog
  reference and a `Price` with equal duty-free and tax-included Money and zero tax.
- Migration `0008` adds four nullable quote columns to `product_order_item` (no new
  table). Each successful add snapshots the selected price's id, name, amount and
  currency in the order transaction. `itemPrice` and `itemTotalPrice` expose this
  snapshot as `OrderPrice` (quantity is one); `orderTotalPrice` sums new add items'
  monthly fees. These are full monthly fees, not prorated first charges or lifetime
  totals. Deletes carry no quote; rejected orders and old add orders whose quotes
  were never recorded omit it. An aggregate is omitted if any add lacks a quote.
  Existing orders are left unchanged rather than reconstructed from today's catalog.
  Downgrading `0008` drops the new quote columns and loses that new history.
- Read-only `GET /billCycle` and `/billCycle/{cycle_id}` project the daily billing
  runs. A cycle's id is its run id, `billingDate` is run-date midnight in BILLING_TZ,
  `billingPeriod` is its ISO business date string, and `validFor` spans that day.
  The list has standard paging/fields and an offset-aware `billingDate` equality
  filter (normalized to BILLING_TZ). A daily run is not an account's monthly period;
  that stays in the bill's `billingPeriod`. Run failures remain visible through the
  internal billing-run API, not invented TMF attributes.
- Every bill has a resolvable `BillCycleRef` to its issuing run, `nextBillDate` equal
  to the end of its own stored `bill_period` (the next cycle day; not recomputed from
  the account's current `billCycleDay`), and tax-excluded/included
  amounts equal to its issued `amountDue`. These historical dates/totals do not move
  with the clock or settlement; negative credit notes retain their negative amounts.
  They are read-only. No bill is created by a new endpoint.

Validation: `tests/test_api_read_completeness.py`, the real-database `0008` migration
round trip in `tests/test_migrations.py`, and the generated-tool drift checks in
`tests/test_mcp_tools.py`. Fields-projected responses still validate every returned
attribute and nested required field; only omitted top-level required fields are
waived by the contract test client.

Local validation (2026-10-14): three full backend runs passed (866, then 869 in the CI
profile, then 869 in the dev profile); the progress collector also passed all 869.
Frontend lint and all 226 component tests passed in both configured time zones;
both Playwright smoke tests passed with the local database migrated to `0008`.
Six deliberate regressions in type checks, comma filters, quote amounts, product
period pricing, navigation completeness and next bill dates caused the expected
test failures and were restored. The final progress report has db 18/18, API 59/59,
UI 19/19 and worker 12/12 implemented and tested. The new endpoints use API stage 2;
an initial unsupported stage label was corrected in the scope and collected metadata
before rendering the report. Generated API/database documentation is deterministic
across two regenerations. `make check` stopped at `docs-check` because that guard
compares generated pages with Git's index, where this uncommitted slice is absent;
the later checks were run independently. No commit or deployment was made.

Built once in `app/api/` and shared by every API. All eight APIs of stage 2 are
implemented; order processing lives in `app/ordering/`.

- **Paths:** `/tmf-api/<apiName>/v5/<resource>`: `productCatalogManagement`,
  `partyManagement`, `customerManagement`, `accountManagement`,
  `productOrderingManagement`, `productInventory`, `customerBillManagement`, and
  `paymentManagement/v4` (TMF676 only exists as v4). `href` is the absolute path, no host.
- **Not exposed:** DELETE anywhere, POST/PATCH on product, cancelProductOrder, `/hub`, the
  billing run. An unsupported method on an exposed path is a 405. **Narrowed 2026-10-16:**
  DELETE exists in TMF620 only, for catalog entries nothing references
  ([catalog.md](catalog.md#deleting-catalog-entries-2026-10-16)); everywhere else it is
  still a 405.
- **Errors:** every non-2xx body is the TMF `Error` (`@type`, `code`, `reason`, `message`,
  `status`), including unknown routes, FastAPI's own validation errors and unhandled
  exceptions (500 says "internal error", nothing more). 400 = malformed JSON, unknown
  attribute, read-only attribute, bad query parameter; 404; 405; 409 = conflicts with
  stored state; 415 = wrong content type; 422 = well-formed but invalid (missing
  mandatory, wrong type or value, reference to something that does not exist). The specs
  do not declare 415 and 422; they are ours.
- **Unknown attributes are rejected (400), never silently dropped.** Always accepted:
  `@type`, `@baseType`, `@schemaLocation`, and `href`, `name`, `@referredType` inside
  references. `id` and `href` are server-managed (400 on POST and PATCH). `lastUpdate` is
  accepted and ignored on POST, because the spec's create schemas make clients send it;
  it is read-only on PATCH.
- **`@referredType` in a reference is checked, never rewritten** (`cloud/quality-1`,
  doc-findings #17). The vendored specs define it as "the actual type of the target
  instance when needed for disambiguation": optional, but when sent it must be the type
  the response states for that reference, else 422 and nothing is stored. Fixed types
  (`app/api/common.py`, `BillingAccountRef`, `CustomerRef`, `CustomerBillRef`,
  `ProductRef`, `ProductOfferingRef`, `ProductOfferingPriceRef`,
  `ProductSpecificationRef`): an order's and its items' `billingAccount`, an item's
  `productOffering` and `product`, a payment's `account` and `paymentItem.item`, an
  account's `relatedParty.partyOrPartyRole` (`Customer`), an offering's
  `productSpecification` and `productOfferingPrice[]`. A customer's `engagedParty` is
  checked against the party it names (`Individual` or `Organization`;
  `check_referred_type`). Not a supertype (`Party` for an Individual is a 422): the
  response would state another value. Same principle as the account's `role` (#3).
  `tests/test_api_referred_type.py`.
- **Money** is `{"unit": "<ISO 4217>", "value": <JSON number>}`, Decimal end to end.
  FastAPI's own body parsing goes through float (proven by a test), so handlers parse the
  raw body with `parse_float=Decimal` (`app/api/deps.py`, `tmfjson.loads`). Responses are
  written by `tmfjson.dumps`, which emits Decimals as number tokens and raises on a float.
  An amount with more than 2 decimals is a 422, never rounded (`19.9900` is accepted: it
  equals `19.99`).
- **TimePeriod** `validFor {startDateTime, endDateTime}` <-> `tstzrange [start, end)`;
  no `endDateTime` = open-ended; `startDateTime` is required on create; date-times must
  carry an offset. Output is UTC (`...Z`).
- **Dates stored as `date`** (product start/termination, order requested start, bill and
  ABR periods): an incoming date-time is converted to the calendar date in `BILLING_TZ`
  and the normalized value is returned (`2026-10-01T23:30:00Z` is `2026-10-02` in Moscow);
  outgoing dates are midnight in `BILLING_TZ`.
- **Lists:** `offset` (default 0), `limit` (default 20, max 100; above the max is a 400),
  ordered by creation. Always 200 - none of the vendored specs declares 206 for a list.
  `X-Total-Count` = all matching rows, `X-Result-Count` = rows in this response.
  `fields=` selects top-level attributes (`id`, `href`, `@type` always); an unknown name
  is a 400. Filters are equality only, on the attributes listed per resource; any other
  query parameter is a 400.
- **POST** returns 201 with `Location`. **PATCH** accepts `application/merge-patch+json`
  only (415 otherwise); `null` removes an optional attribute and is a 422 on a mandatory
  one; `@type` may be repeated but not changed.
- **Transactions:** one per request (`app/api/deps.py`, `Tx`), committed when the handler's
  block ends, rolled back if it raises.
- **Clock:** the `get_clock` dependency; tests override it with `FixedClock`.
- **The contract is the vendored TMF specs, not FastAPI's `/openapi.json`.** Response
  bodies are plain dicts written by `tmfjson` (no Pydantic response models), so the
  generated `/docs` has no response schemas - on purpose. Stage 5 generates the frontend
  types from `spec/*.yaml` (and the extension schemas we serve), never from `/openapi.json`.
- **Contract-test limit:** a discriminator that sits next to `allOf` (the TMF subclass
  pattern) is not dispatched on, so a wrong `@type` on a reference (e.g. `IndividualRef`
  where the spec says `PartyRef`) passes schema validation. The API tests assert those
  `@type` values explicitly.
- **Contract tests:** `tests/apiclient.py` validates every response in the suite against
  the schema in `spec/` for its path (`Error` for non-2xx). `tests/contract.py` uses
  openapi-schema-validator's OAS 3.0 dialect with its discriminator handling replaced:
  upstream recurses forever on the TMF v5 schemas (each has a self-mapping discriminator
  next to `allOf`), and plain JSON Schema cannot validate the `...RefOrValue` `oneOf`s.
  Because it overrides the library's keyword handlers, `openapi-schema-validator` is
  pinned to an exact version in `requirements-dev.txt`; re-run the suite when bumping it.

### TMF632, TMF629, TMF666 mapping

| Resource | DB column <-> TMF attribute | Filters |
|---|---|---|
| individual | `party` with `party_type = 'Individual'`: `given_name` <-> `givenName`, `family_name` <-> `familyName` (both mandatory, as in the spec's create schema) | `givenName`, `familyName` |
| organization | `party` with `party_type = 'Organization'`: `organization_name` <-> `name` | `name` |
| customer | `name`, `status` (`Created`/`Active`/`Inactive`, ours), `party_id` <-> `engagedParty` (`PartyRef`, `@referredType` = the party type; fixed at creation) | `name`, `status`, `engagedParty.id` |
| billingAccount | `name`, `state` (`Active`/`Suspended`/`Closed`, ours), `customer_id` <-> `relatedParty[0]` (role `customer`, required exactly on POST - 422 otherwise; `PartyRoleRef` to the Customer; exactly one, fixed at creation), `bill_cycle_day` <-> `billCycleDay`, `currency` <-> `currency`, computed <-> `accountBalance[]`, `credit_limit` <-> `creditLimit` (0003; see below) | `name`, `state`, `relatedParty.id` |

An individual is not reachable under `/organization` and vice versa (404).

- **Extension `BssBillingAccount`** (TMF extension pattern). Responses carry
  `@type: BssBillingAccount`, `@baseType: BillingAccount` and `@schemaLocation:
  /tmf-api/schema/BssBillingAccount.schema.json`, a JSON Schema served by the app that
  adds `billCycleDay` (integer 1..28) and `currency` (ISO 4217). Both are required on
  create. Both are fixed after creation: `currency` for good (PATCH is a 400, read-only),
  `billCycleDay` for now (PATCH is a 409 with the reason; see ["Open questions"](billing-core.md#open-questions)). TMF666 has no currency attribute at all, and
  `billStructure.cycleSpecification.billingDateShift` means an offset of the bill date
  from the period start, not the day the period starts - hence the extension. PATCH
  accepts `@type` `BssBillingAccount` or `BillingAccount`. Every full billingAccount body
  in the tests is validated against both TMF666 and the served extension schema.
- **`creditLimit` (migration 0003)** is TMF666's own Money attribute, always in the
  account's currency (another `unit` is a 422), never negative (422), optional; PATCH
  `null` removes it. **Informational only:** nothing - ordering included - is ever
  refused because of it; the admin UI shows it and warns.
- **Balance** is the spec's `accountBalance[]` with exactly one read-only entry:
  `{"balanceType": "currentBalance", "amount": Money, "@type": "AccountBalance"}`,
  computed on every read as payments (`done`) minus all ABRs. **Positive = the customer
  is in credit** (has paid more than was charged); negative = the customer owes.
  `accountBalance` in a POST or PATCH is a 400.

### TMF676 (v4) mapping

| payment | `billing_account_id` <-> `account` (AccountRef, `@referredType: BillingAccount`), `amount` + `currency` <-> `totalAmount` and `amount` (equal: no taxes), `received_at` <-> `paymentDate` and `statusDate` (the clock at POST), `status` (`done`), `payment_method` <-> `paymentMethod` (`{name, @type}`, both the stored word) | `account.id`, `status` |
|---|---|---|

POST records a received payment: `account`, `totalAmount` and `paymentMethod` (with a
`name` or an `@type`) are mandatory, as in the spec. `amount`, if sent, must equal
`totalAmount`; `taxAmount`, if sent, must be zero. `totalAmount.unit` must be the
account's currency (422); the composite FK behind it maps to 409 if it ever fires.
Payments are never changed or deleted (405).

**A payment settles at once** (bill lifecycle slice 3): POST takes the billing
account's row lock (`FOR UPDATE`, as `bill_account` does) before inserting, then
applies its `paymentItem[]` (explicit lettering: `{item: {id, @referredType:
CustomerBill}, amount}`; `totalAmount` if sent equals `amount`, `taxAmount` zero) and
then the oldest open bills - one transaction. 422: an item's bill unknown or another
account's, the same bill twice, an amount <= 0 / > 2 decimals / in another currency,
`@referredType` not CustomerBill, items above `totalAmount`; 409: an item above the
bill's remaining amount, or a bill on hold or settled. A refused item stores nothing.
Responses carry `paymentItem[]` = every allocation from the payment summed per bill
(absent while none) - it **grows** when the payment's unused credit pays later bills.
**The lock is needed for payment vs payment only**: the payment's foreign key already
takes a key-share lock on the account row, which waits on a run's `FOR UPDATE` but not
on another payment's key share (proven: without the explicit lock two overlapping POSTs
both read the same open bill; `test_two_payments_settle_one_after_the_other` fails).

### TMF637 and TMF678 mapping

| Resource | DB column <-> TMF attribute | Filters |
|---|---|---|
| product | `status`, `start_date` <-> `startDate`, `termination_date` <-> `terminationDate` (exclusive: the first day not billed), the creating order's `order_date` <-> `creationDate` (the injected clock; not `created_at`), offering name <-> `name`, `billing_account_id` <-> `billingAccount`, `product_offering_id` <-> `productOffering`, `created_by_item_id` / `terminated_by_item_id` <-> `productOrderItem[]` (`RelatedOrderItem`, role `creation` / `termination`) | `billingAccount.id`, `status` |
| customerBill | `bill_date` <-> `billDate`, `bill_period` <-> `billingPeriod`, `amount_due` + `currency` <-> `amountDue`, amount_due - allocations (0 for a credit note) <-> `remainingAmount`, `payment_due_date` <-> `paymentDueDate`, payment allocations <-> `appliedPayment[]` (`{appliedAmount, payment: PaymentRef}`; a credit note's credit is not a payment and not listed), `category` (`normal`/`creditNote`), `onCycle` <-> `runType`, `BL-` + 8 hex <-> `billNo`, `state`, `billing_account_id` <-> `billingAccount` | `billingAccount.id`, `state` |
| appliedCustomerBillingRate | `charge_type` <-> `appliedBillingRateType` (`recurring` -> `appliedBillingCharge`, `refund` -> `appliedBillingCredit`), `amount` + `currency` <-> `taxExcludedAmount` (refunds negative), `period` <-> `periodCoverage`, `customer_bill_id` <-> `bill` and `isBilled`, `product_id` <-> `product`, `billing_account_id` <-> `billingAccount`, `applied_at` <-> `date` (the injected clock of the run's attempt that wrote it, as its bills' issue; migration 0007 - before it, `created_at`) | `billingAccount.id`, `product.id`, `bill.id`, `isBilled` |

- Products are only written by orders; POST/PATCH/DELETE on `/product` are 405.
- Bills and ABRs are only **created** by the billing run (`app/billing/adapter.py`),
  which the worker's daily run calls per account. ABRs are read-only; a bill's **state**
  changes with settlement (the run, payments) and by **`PATCH /customerBill/{id}`**
  (merge patch, `state` only - the spec's `CustomerBill_MVO`; any other attribute 400):
  `onHold` from `sent`/`partiallyPaid`; from `onHold` only the state its allocations
  imply (`sent` if nothing paid, else `partiallyPaid`; strict - anything else is a 409
  naming it); repeating the current state is a 200 no-op; every other change a 409. The
  operator (X-Remote-User) goes into the state history. A release settles the account.
- **Status flips:** a product ordered with a future start date is `created` until the
  worker's run of its start date flips it to `active` (`products.flip_statuses`, see
  ["Billing worker"](worker.md#billing-worker-stage-4-in-progress)); until that run, on the start date itself, it still reads `created`.
  Billing is driven by dates, not status, so money is unaffected.
- **Likewise `pendingTerminate`:** a delete of a started product with a termination date
  in the future sets `pendingTerminate`; the worker's run of that date flips it to
  `terminated`. A termination date of today sets `terminated` directly. The product is
  billed up to the day before `termination_date` either way.

### TMF622 mapping and order processing

| productOrder | `state`, `order_date` <-> `creationDate`, `completion_date` <-> `completionDate`, `requested_start_date` <-> `requestedStartDate` (the effective date: today when omitted), `billing_account_id` <-> `billingAccount` (mandatory - ours), items <-> `productOrderItem[]` (`item_id` <-> `id`, `action`, `state`, `product_offering_id` <-> `productOffering`, `product_id` <-> `product`; `quantity` is always 1), `product_order_error_message` <-> `productOrderErrorMessage[]` (`code`, `reason`, `timestamp`, `productOrderItem[]` refs) | `state`, `billingAccount.id` |
|---|---|---|

- **GET list, GET by id, POST only.** PATCH/DELETE are 405, cancelProductOrder is 404.
- **Synchronous, one transaction** (`app/ordering/process.py`). States come from the pure
  function in `app/ordering/states.py`: order `acknowledged -> inProgress -> completed`
  or `acknowledged -> rejected`; items the same, plus `acknowledged -> cancelled`.
- **4xx vs rejected.** Malformed JSON, an unknown attribute or an empty
  `productOrderItem[]` is a 400; a missing mandatory field, a reference to a billing
  account, offering or product that does not exist, duplicate item ids, a product in two
  delete items, `quantity` other than 1, an add item without `productOffering` (or with a
  `product`) and a delete item without `product` (or with a `productOffering`) are 422 -
  nothing is stored. Everything else that is wrong is a business rejection: the order is
  stored as `rejected` with its reasons and the answer is **201**.
- **Rejection codes** (ours, in `productOrderErrorMessage[].code`): order level -
  `accountNotActive`, `requestedStartDateInPast`; item level - `actionNotSupported`
  (modify, noChange), `billingAccountMismatch` (item account differs from the order's),
  `offeringNotSellable` (not `isSellable` or not `Active`), `offeringNotValid` (validFor
  does not cover midnight of the start date), `noRecurringPrice` (none in the account's
  currency at that midnight), `priceEnds` (from the start date, the offering's recurring
  prices in the account currency do not form a gapless chain ending open-ended - the
  whole chain is walked, not just the price at the start; D-W20), `productOfOtherAccount`,
  `productAlreadyEnded`, `openDeleteItem`, `productNotStarted`.
- **All or nothing.** One reason anywhere rejects the order: failing items are
  `rejected`, the rest `cancelled`, nothing is executed. A rejected order is decided
  before its rows are inserted, so its items are stored directly in their final states -
  a rejected delete item must never be stored open, or it would collide with the open
  delete it is rejected for (the `product_order_item_one_open_delete` index would abort
  the transaction and the rejected order could not be stored).
- **Row lock.** The products of delete items are locked (`SELECT ... FOR UPDATE`, in id
  order) before anything about them is read. A concurrent second delete waits, then sees
  the product terminated and is rejected with `productAlreadyEnded` (proven with two
  connections in `tests/test_order_concurrency.py`; without the lock it waits on the
  product UPDATE instead and terminates the product a second time).
- **Dates.** `requestedStartDate` is converted to the date in BILLING_TZ and returned
  normalized (`2026-10-01T23:30:00Z` -> product `startDate` 2026-10-02). It defaults to
  today and may not be in the past. **Our interpretation:** it is the start date of
  `add` items and the termination date of `delete` items (TMF has no per-item date).
- **add** creates the product with `created_by_item_id`; status `active` if it starts
  today, `created` if later (the worker flips it; "Status flips" above). The item then
  references it.
- **delete** of a started product sets `termination_date` (exclusive),
  `terminated_by_item_id` and status `terminated` (termination today) or
  `pendingTerminate` (termination in the future; flipped by the worker, as above).
  **delete of a product that has not started** cancels it: status `cancelled`, `termination_date = start_date` (0 billable days); a
  requested date after its start date is rejected (`productNotStarted`).
