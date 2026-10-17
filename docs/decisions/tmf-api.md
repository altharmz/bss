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

### Following TMF (2026-10-17)

The human's rule for closing [the gap analysis](../tmf-gap-analysis.md): **what the
vendored specs in `spec/` define is implemented; what they do not define is skipped.**
"TMF" means the eight vendored specs (the human accepted this reading): TMF630's filter
operators and `sort`, and APIs we have not vendored (TMF635, TMF670, ...), stay out
until a spec for them is vendored. The rule supersedes, each in the slice that closes
it: taxes and `modify` items being out of scope (D4 in the gap analysis), payment
processing (D5: payment states other than `done`), merge-patch only (D6), and our own
requirements the specs do not make (offering `validFor`, an order's `billingAccount`,
the account's `billCycleDay` and `currency`, ...) - kept only where billing cannot work
without them, with the reason recorded. Where a spec defines the shape but not the
behaviour (suspension, tax rates, `modify` proration, payment state transitions,
discounts), a design note is approved before the slice.

**The gap list is exact and locked.** `tests/tmf_gaps.py` probes the app through HTTP:
every attribute of every POST/PATCH request schema (and one level below), sent alone
(`refused` = unknown attribute, `read-only`), the attributes an empty POST requires that
the spec does not (`requires`), every top-level attribute of each resource schema
through `fields=` (`missing`), every PATCH content type a spec lists (`content-type`),
and the API name/version of each spec's server URL (`base path`). A control attribute
must be refused at each probed place, else the place is reported `unprobed`.
`tests/test_tmf_gaps.py` requires the result to equal `tests/tmf-gaps.txt` line for line
(831 lines on 2026-10-17): a slice that closes a gap removes its lines, and nothing opens
unnoticed. Behaviour the probe cannot see - states never reached, values rewritten
(`paymentMethod.@type`), item order, nested response attributes - is listed in the gap
analysis. Negative proofs: accepting `description` on POST customer fails the lock with
that line "closed"; a broken control check fails both probe tests.

Slices, in order: (1) nothing dropped or rewritten (`@baseType`/`@schemaLocation`
persisted, `engagedParty` on PATCH customer, `paymentMethod.@type`, item
`billingAccount`, item order); (2) PATCH formats (`application/json`, JSON Patch,
JSON-Patch-Query); (3) base paths `customer/v5`, `payment/v4`; (4) TMF632/629
attributes; (5) TMF666; (6) TMF620; (7) taxes; (8) TMF622 (`modify` items, states);
(9) TMF637 (suspension); (10) TMF676 (payment states and attributes).

### Product writes through orders (2026-10-17)

The last seven operations, approved by the human on 2026-10-17 ("reach 100%"), close
coverage at 129/129. **TMF637 product writes keep "orders alone create or end
products"**: each one places a TMF622 order on the caller's behalf, in the same
transaction, through `place_order` - an ordinary order, visible in TMF622, with its
events.

- **`POST /product`** (`billingAccount`, `productOffering`, optional `startDate` - today
  if absent): an add order. 201 with the product.
- **`PATCH /product/{id}`**: `terminationDate` only - a delete order with that date
  (exclusive, as everywhere). Everything else is read-only (400); `null` is a 422
  (taking an end back is cancelProductOrder's job); a product that already ends is a
  409 naming the way out.
- **`DELETE /product/{id}`**: a delete order effective today - a started product ends
  today, one that has not started is cancelled. 204. **Deviation from REST, chosen by the
  human:** the product stays readable as `terminated`/`cancelled` - it is billing history.
- **An order that would be rejected** is a 409 with its reasons (`code: reason`), and
  nothing is stored: the order runs in a savepoint that is rolled back (a direct order
  POST still stores rejected orders, as before). Reference problems are a 422.
- Tests: `tests/test_api_product_writes.py`. Negative proof: without the savepoint
  rollback a rejected add answers 500 instead of 409.

### Billing cycle specification writes (2026-10-17)

`POST`, `PATCH`, `DELETE /billingCycleSpecification`; migration `0019`
`billing_cycle_specification` and `billing_account.cycle_specification_id`. **A stored
specification is a named instance of the one policy** (the human's choice): billingPeriod
and frequency `monthly`, billingDateShift `0` - anything else is a 422, and the table's
CHECKs say the same. Billing never reads it. The built-in `monthly` is not stored and
answers 409 to PATCH and DELETE. PATCH changes name and description. An account's
`billStructure.cycleSpecification` is writable now (`monthly`, a stored id, or null =
monthly; an unknown one is a 422), taking the specification FOR KEY SHARE; DELETE is a
409 while an account names it. Events: BillingCycleSpecification Create,
AttributeValueChange, Delete. Tests: `tests/test_api_cycle_specification.py`. Negative
proof: without the account reference check the 409 loses its reason.

### PATCH productOrder (2026-10-17)

Migration `0020` adds `description`, `category`, `priority` (`0` highest .. `4`) and
`externalId[]` (ExternalIdentifier: `id`, `owner`, `externalIdentifierType`) to
`product_order`; POST accepts them, PATCH changes only them (null removes), on an order
in any state. Everything processing decided - state, items, dates, account, totals,
cancellation - is read-only (400). Emits ProductOrderAttributeValueChangeEvent. Tests:
`tests/test_api_order_patch.py`. Negative proof: with null ignored, removing an attribute
keeps it.

### Order cancellation and deleting rejected orders (2026-10-17)

TMF622 `cancelProductOrder` (POST, GET list with `productOrder.id`, GET by id) and
`DELETE productOrder`. Migration `0016` adds `cancel_product_order` (one per order) and
`product_order.cancellation_date` / `cancellation_reason` (TMF622 cancellationDate /
cancellationReason; set exactly when the order is cancelled, CHECK-enforced).
**Rollback:** `alembic downgrade 0015` drops the table and columns but keeps cancelled
orders as `cancelled` (0015's code never acts on them); upgrading again gives them back a
`cancellation_date` of their completion date, so the CHECK holds
(`tests/test_migrations.py::test_0016_order_cancellation_round_trip`).

**What a cancellation means here.** Orders complete synchronously, so there is never an
order in progress to stop. A cancellation undoes a **completed** order whose every effect
is still in the future (`app/ordering/cancel.py`):

| Item | Cancellable while | Then |
|---|---|---|
| add | its product has not started (`created`/`pendingActive`, start after today) and no other order ended it | product `cancelled`, `termination_date = start_date` (zero billable days), no terminating item |
| delete | the termination it set has not happened (after today) and is still the product's | product open-ended again: `active`, or `created` if the delete had cancelled a product that has not started; `terminated_by_item_id` cleared |
| either | billing wrote no ABR of the product for the cycle period containing the item's date (start or termination) or a later one | - |

The last rule exists because the billing core never charges more than it charged
(`InconsistentState`): a termination already reflected in a partial charge cannot be
taken back. Order and items go `completed -> cancelled` (the state machine gains exactly
these two transitions; `completed` is no longer terminal). `requestedCancellationDate`
must be absent or today (BILLING_TZ), else 422: a cancellation takes effect when it is
requested. A missing order is a 422 (a reference in the body), an order that cannot be
cancelled - not completed, or any item no longer reversible - a **409 naming every
reason, with nothing stored** (the human's choice of 2026-10-17; CancelProductOrder has
no field for reasons). So every stored cancellation is `done`, with
`effectiveCancellationDate = creationDate`.

**Billing replays.** The cancellation changes the product rows in place, so a retry of
an earlier failed run made after it sees the product as it is now. That changes nothing
billed: the rules above allow a cancellation only when billing never charged the periods
involved, and a run that failed for the account wrote nothing.

**Locks**, in the order billing and orders take them: the order `FOR UPDATE`, then the
account `FOR UPDATE` (as `bill_account`: no run computes the account in between), then the
products in id order (as orders and the status flips). A charge already written also holds
its product through the ABR's foreign key; the account lock covers a run that has read the
products but not written yet.

**DELETE productOrder** removes a `rejected` order with its items and reasons (it never
executed, nothing refers to it), 204; any other state is a 409 (a completed order is its
products' history; it can be cancelled instead). That frees catalog entries only a
rejected order referred to.

**UI:** a product row pending termination offers "Undo deactivation"
(`ui.subscriber.undoDeactivation`): it reads the product's `termination` order item from
TMF637 and posts a cancelProductOrder with only `productOrder`; a 409's reason shows in the
row. The UI's own Deactivate is effective today, so the button appears for terminations
ordered for a later date (through the API or MCP).

MCP gets `cancel_order` and `delete_order` (write) and `list_cancellations`,
`get_cancellation` (read). Coverage: 105/129 non-listener operations (81.4%). Tests:
`tests/test_api_order_cancel.py`, `tests/test_ui_requests.py::test_undo_deactivation_as_the_ui_sends_it`,
SubscriberCard component tests. Negative proofs, each reverted: without the account lock
the cancellation does not wait for a run holding the account; without the charge check a
termination November's charge already reflects is taken back (201) - in the race test too;
without the rejected-only check DELETE of a completed order is a 500 (foreign keys);
with the marker renamed the component test fails.

### Deleting parties, customers and accounts (2026-10-17)

The catalog's delete rule ([catalog.md](catalog.md#deleting-catalog-entries-2026-10-16))
extends to TMF632, TMF629 and TMF666: `DELETE` on individual, organization, customer,
billingAccount, financialAccount, partyAccount, settlementAccount, billFormat and
billPresentationMedia answers 204 (no body, no query parameters - any is a 400) when
nothing references the entry, else a 409 naming the references, with nothing changed.
No migration: the existing `NO ACTION` foreign keys back every check (a reference the
checks missed is a 409 "still referenced", never a dangling row).

| Entry | 409 while | Removed with it |
|---|---|---|
| individual / organization | a customer engages it, or it owns a financial, party or settlement account | - |
| customer | it has a billing account | - |
| billingAccount | it has history: any order (rejected ones too), product, ABR, bill, on-demand request, payment, refund, bill allocation, or an outcome of a billing run (`parties.ACCOUNT_HISTORY`) - close it instead | its presentation media preferences |
| financialAccount | a billing, party or settlement account links to it | - |
| partyAccount / settlementAccount | never (nothing refers to one) | - |
| billFormat / billPresentationMedia | a billing account prefers it (issued bills keep their own JSON snapshot, no reference) | - |

**What it is for:** entries created by mistake. A billing account becomes undeletable
as soon as a billing run has processed it: every run records an outcome for each account
opened before its date, even one without products. In practice an account can be
deleted on the day it was opened, or until its first billing run.

**Concurrency.** Deletes lock their row `FOR UPDATE`. Writers that are about to
reference a row take it `FOR KEY SHARE` until commit: customer POST its party, financial,
party and settlement account POST their owner (`owner_party`), billing account POST its
customer, `billStructure` its format and media, orders and on-demand requests their
account. Writers that lock the account `FOR UPDATE` anyway (billing account PATCH,
payment, refund) take that lock up front instead, because a `KEY SHARE` lock upgraded
later would deadlock against a second writer doing the same. Links to a financial account
were already `FOR SHARE`. So a delete either waits for the writer and then sees its row
(409), or goes first and the writer sees the entry gone (422 for a POST's reference).
**The worker** lists a run's members when the run starts; `DateRun.process` now locks
the account (`FOR UPDATE`, as `bill_account` does) and skips it if it is gone - nothing
is billed or recorded, and `close_run` counts only accounts that still exist.

MCP gets nine write-scope tools (`delete_individual`, `delete_organization`,
`delete_customer`, `delete_account`, `delete_financial_account`,
`delete_party_account`, `delete_settlement_account`, `delete_bill_format`,
`delete_bill_media`). The shared helpers moved from `app/api/catalog.py` to
`app/api/deletes.py`. Coverage: 101/129 non-listener operations (78.3%). Tests:
`tests/test_api_deletes.py`. Negative proofs, each reverted: without the customer's
`FOR KEY SHARE` on its party, a customer racing a party delete is a 500 instead of a 422;
without the payment's up-front `FOR UPDATE`, a payment racing an account delete is a 500
(LookupError in `load_account`); without the worker's check, a run whose member was
deleted aborts with a LookupError; without `billingRun` in `ACCOUNT_HISTORY` the
foreign key still refuses the delete, but the 409 no longer names the reason.

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
  still a 405. **Narrowed again 2026-10-17:** the same rule for parties, customers and
  the TMF666 resources ([below](#deleting-parties-customers-and-accounts-2026-10-17)),
  and for rejected orders; cancelProductOrder is exposed since the same day, and so is
  `/hub` in every API ([events.md](events.md)). **And on the same day the rest:**
  POST/PATCH/DELETE on product as orders placed on the caller's behalf, writes of
  billingCycleSpecification, PATCH productOrder ([above](#product-writes-through-orders-2026-10-17)).
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
  **Changed 2026-10-17:** DELETE removes a rejected order, and cancelProductOrder exists
  ([below](#order-cancellation-and-deleting-rejected-orders-2026-10-17)); PATCH stays 405.
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
