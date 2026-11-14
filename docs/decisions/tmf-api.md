# TMF API: conventions and mappings (TMF632, 629, 666, 676, 637, 678, 622)

GUI review G4a (I2): an offering-change modify item rejects with
`numberRequirementDiffers` when the target specification's MSISDN requirement differs
from the product's immutable `requires_msisdn`. Both directions are refused; equal
requirements remain accepted across specifications. This uses the existing 201 rejected
order/error-message shape, preserves number claims, and requires no migration. The UI
offers only same-specification alternatives; API clients may still change between
specifications whose number requirements agree. Four backend cases pin this rule.

The approved [refund/bill-delivery milestone](billing-completion.md) adds TMF676
refunds, TMF678 customerBillOnDemand, and TMF666 billFormat/billPresentationMedia.
It extends account billStructure with writable format/media preferences while
keeping cycleSpecification read-only. Cash payouts reduce balance and unused
credit; issued bills carry immutable JSON attachments. These decisions extend
the original mappings below. TMF operation coverage becomes 67/129 (51.9%).

Index: [README.md](README.md). The TMF620 catalog
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
unnoticed. It runs in parts that xdist spreads over its workers (2026-10-23: the whole
probe was one 65 s test at the end of the run): one per API on the empty database, and
`nested`, which alone populates it. Every line starts with its API, so the parts never
overlap, and a test checks that every recorded line belongs to one. Behaviour the probe cannot see - states never reached, values rewritten
(`paymentMethod.@type`), item order, nested response attributes - is listed in the gap
analysis. Negative proofs: accepting `description` on POST customer fails the lock with
that line "closed"; a broken control check fails both probe tests.

Slices, in order: (1) nothing dropped or rewritten (`@baseType`/`@schemaLocation`
persisted, `engagedParty` on PATCH customer, `paymentMethod.@type`, item
`billingAccount`, item order); (2) PATCH formats (`application/json`, JSON Patch,
JSON-Patch-Query); (3) base paths `customer/v5`, `payment/v4`; (4) TMF632/629
attributes; (5) TMF666; (6) TMF620; (7) taxes; (8) TMF622 (`modify` items, states);
(9) TMF637 (suspension); (10) TMF676 (payment states and attributes).

### Nothing dropped or rewritten (2026-10-17)

Slice 1 of "Following TMF", the three choices approved by the human. Migration `0021`.
126 lines left `tests/tmf-gaps.txt` (831 -> 705).

- **`@baseType` and `@schemaLocation`** are stored as sent on every resource a client
  creates (`base_type`, `schema_location` on 20 tables; empty is a 422), returned,
  changed by PATCH (null removes) and selectable with `fields=` on every resource. The
  billing account keeps its extension's fixed values. Resources only the server makes
  (bills, ABRs, bill cycles) select them without a value. `@type` stays one the spec
  names for the resource: client subclasses are not defined by the specs.
- **A client may choose the `id`** on every POST whose spec schema has one
  (Addressable_FVO): a UUID not in use (409 if taken, 422 if not a UUID - ours are
  UUIDs, a restriction of the spec's string). `claim_id` serializes two requests for
  the same id with an advisory lock. POST /product passes it to the product its order
  creates.
- **Server-set attributes** the specs let a client send (a job's status and dates, an
  account's balance and relationships, `lastUpdate`, an order's state, totals and
  messages, a product's derived references, a refund's date, a bill's run attributes,
  a cancellation's state) are accepted when they equal the server's value and a 422
  otherwise (`take_server_set` before parsing, `check_server_set` against the current
  body on PATCH, against the new resource on POST - the 422 rolls it back). What a
  client could really change but we do not yet (an offering's specification, a
  price's amount, an order's items, a product's dates, an account's owner) stays a
  400 read-only, for slices 4-9. The gap probe learns from `common.on_server_set`
  (test instrumentation only) which attributes a request took this way.
- **Payments keep `paymentMethod.name` and `@type` apart** (`payment_method_name`,
  `payment_method_type`; one of them at least). Existing payments answer as before (the
  one word in both). `payment_method` stays the UI's one-word label.
- **Order items come back in the order sent** (`product_order_item.position`; existing
  items numbered in their old text order of `item_id`) and **carry the order's
  `billingAccount`** - it must equal the order's, so no column.
- **A customer's `engagedParty` may change while it has no billing account** (409
  otherwise: the accounts' financial accounts belong to the party); the customer is
  locked FOR UPDATE, so an account created meanwhile waits. `id`/`href` in a customer
  PATCH (Customer_MVO has them) and `href` on POST are accepted when unchanged.
- An empty merge patch of a bill is a 200 no-op, as for every other resource.

Tests: `tests/test_api_tmf_meta.py` (every client-created resource: meta round trip,
chosen id), `test_0021_meta_payment_method_and_item_position`, and the updated
read-only cases across the suite. Negative proofs: items ordered by `item_id` again,
`check_server_set` inverted, and the engagedParty account check inverted each failed
`tests/test_api_tmf_meta.py`. Rolling back `0021` needs `alembic downgrade 0020` by
hand (drops the meta, method and position columns).

### PATCH formats (2026-10-17)

Slice 2 of "Following TMF"; retires D6 (merge patch only). No migration. 54 lines left
`tests/tmf-gaps.txt` (705 -> 651). Every v5 spec lists four PATCH content types, and
every PATCH takes them (`app/api/deps.py`, `merge_patch_body`):

- `application/json` and `application/merge-patch+json`: a merge patch, as before.
- `application/json-patch+json` (RFC 6902: add, remove, replace, move, copy, test) and
  `application/json-patch-query+json` (TMF630: a path may end in `?attr=value[&...]`,
  selecting the elements of the first array on it, e.g.
  `/externalId/owner?owner=Shop`): applied to the resource as its own GET answers it
  (an in-process GET through the app), and the top-level attributes that changed go
  to the handler as a merge patch. So read-only, server-set, unknown and invalid
  values are refused exactly as in a merge patch; a nested change sends that
  attribute's whole new value. `app/api/jsonpatch.py`.
- Errors: not an array of operations, an unknown op, a path that is not a pointer, a
  missing value/from, a malformed query - 400; a path that leads nowhere, an index out
  of range, a query that selects nothing, a result that is no longer an object - 422;
  a failed `test` - 409. Nothing is stored on any error. In plain JSON Patch `?` is
  part of a key, as RFC 6901 says.
- The GET and the PATCH run in separate transactions: a concurrent change of the same
  attribute in between is overwritten, as with a merge patch sent after a GET.
  `test` operations check what was read.

Tests: `tests/test_api_patch_formats.py`. Negative proofs: refusing `application/json`
again, and skipping the `test` comparison, each failed it.

### Base paths (2026-10-18)

Slice 3 of "Following TMF". No migration. Two lines left `tests/tmf-gaps.txt` (651 ->
649). Each API is served under the API name and version of its spec's `servers` URL
(TMF676 v4: `basePath`): TMF629 moved from `/tmf-api/customerManagement/v5` to
`/tmf-api/customer/v5`, TMF676 from `/tmf-api/paymentManagement/v4` to
`/tmf-api/payment/v4`. The other six already matched. Where a spec's example hrefs
disagree with its `servers` (TMF629's say `customerManagement`, TMF632's `party/v5`),
`servers` wins: it is what a generated client uses.

- **A straight rename, no aliases** (the human's OK, 2026-10-18). The deployed instance
  still serves the old paths, but its only clients - the UI and the MCP connector - are
  deployed with the app and move with it. The old paths are a 404. An outside client
  of the demo instance (none known) would need the new base URL.
- Stored data holds no paths (an `href` is built when a resource is answered), so no
  migration rewrites anything; event payloads already in the outbox keep the `href` they
  were made with.
- Moved with it: the UI (`frontend/src/api/bss.ts` and its tests), the MCP tool families
  (`bss_mcp/operations.py`; tool names do not contain the path), the event types'
  bases, `docs/progress/scope.yaml` paths (ids unchanged), `docs/api/` (generated).

Tests: the gap lock (`tests/test_tmf_gaps.py`) with the two `base path` lines removed,
and `test_old_base_paths_are_gone` in `tests/test_api_conventions.py`. Negative proof:
the old TMF629 base in `app/api/customer.py` failed the suite.

### Party and customer attributes (2026-10-18)

Slice 4 of "Following TMF", the plan approved by the human (2026-10-18, "go"). Migration
`0022`. 173 lines left `tests/tmf-gaps.txt` (649 -> 476): every attribute of
`Individual`, `Organization` and `Customer` is taken on POST and PATCH and returned
(`fields=` included). TMF632 and TMF629 have no gap lines left.

- **Documents.** Attributes with no behaviour here - a party's `contactMedium`,
  `birthDate`, `partyCharacteristic`, `tradingName`, ...; a customer's `description`,
  `role`, `validFor`, `statusReason`, `contactMedium`, `characteristic`, `agreement`,
  `paymentMethod`, `creditProfile`, `relatedParty`, `partyRoleSpecification` - are
  stored as sent in one jsonb column (`party.details`, `customer.details`; the human
  chose one column over ~35 typed ones) and returned as stored. Billing never reads them.
  The lists in `app/api/party.py` / `customer.py` are locked to the spec schemas
  (`test_every_spec_attribute_has_a_place`).
- **Checked against the vendored spec itself** (`app/api/specschema.py`), not restated
  as Pydantic models - the nested schemas are many and polymorphic: `$ref`, `allOf`,
  `required` (a nested object's `@type` too, where the `_FVO`/`_MVO` schema requires
  it), `type`, `enum`, date-time with a time zone. **Unknown attributes are a 400 at
  any depth** (`contactMedium.0.fax: unknown attribute`), so the gap probe sees the
  level below too. POST checks the `_FVO` schema, PATCH the `_MVO`. Date-times are
  stored and returned in UTC, numbers as exact decimals (jsonb read back with
  `parse_float=Decimal`, `app/db.py`). An object schema that declares no properties
  (an ObjectCharacteristic's `value`) takes anything.
- **Subclasses.** A `discriminator` selects the schema its mapping names for the
  `@type` sent (an `EmailContactMedium` knows `emailAddress`, a `PhoneContactMedium`
  does not). A `@type` the mapping does not name is read as the base schema, as plain
  JSON Schema reads the `allOf` (`tests/contract.py` does the same): the specs' own
  examples use such names (TMF629's Customer_Create_example sends `relatedParty` as
  `RelatedPartyRefOrPartyRoleRef`), and the base still refuses what it does not
  declare. A `oneOf` has no base: its `@type` must be one it maps (422).
- **The spec ships in the image** (`Dockerfile`: `COPY spec/*.yaml spec/*.json`,
  ~2.5 MB; `PyYAML` pinned in `requirements.txt`, it was already in `constraints.txt`).
  The human chose this over a generated extract under `app/`. Slices 5-10 reuse the
  checker for the nested attributes of TMF666, TMF620, TMF622, ...
- **Merge patch of a document** replaces it whole; `null` removes it. On POST a `null`
  is nothing sent. JSON Patch reaches inside a document as usual (slice 2: applied to
  the GET body).
- **Party `status`** is a column (`party.status`, CHECK: `initialized`/`validated`/
  `deceased` for an individual, `initialized`/`validated`/`closed` for an organization),
  optional - existing and new parties have none unless sent. **A record only** (the
  human's choice): any transition, and `deceased`/`closed` block nothing (customers,
  orders, billing). A change emits the `...StateChangeEvent`; a list may filter by it.
- **References inside documents are stored as sent, not resolved** (the human's
  choice): `relatedParty`, `agreement`, `paymentMethod`, `partyRoleSpecification`, ...
  mostly point to APIs not vendored here (TMF651, TMF670, TMF669, TMF673), and resolving
  the ones to our own parties would make every party DELETE search jsonb.
  `engagedParty` stays checked as before.
- **Customer `account`** is the customer's billing accounts, linked from the account's
  side (`relatedParty` customer): server-set as in slice 1 - accepted on POST and PATCH
  when it equals what we answer (empty on POST), a 422 otherwise.
- **Customer `validFor`** is a record only: it stops neither orders nor billing.
  An individual's `name` and `formattedName` are stored as sent; references to the
  party (`engagedParty.name`, ...) keep "given family".
- **MCP:** the request models carry the documents as untyped fields
  (`common.documents_model`), so the tools list them; `parse_documented` checks them.
  A JSON float stays refused by the connector (a decimal characteristic value needs the
  API directly).

Tests: `tests/test_api_party_attributes.py` (round trips, the spec's own customer
example, subclasses, unknown/required/type/enum/format problems, merge and JSON patch,
exact numbers, status events), `test_0022_party_status_and_details`, and the gap lock.
Negative proofs: without the nested unknown check the lock fails with `unprobed` lines
opened and the 400 cases fail; without the discriminator selection the round trips fail;
with `null` ignored in a document patch the removal test fails. Rolling back `0022`
needs `alembic downgrade 0021` by hand (drops `status` and both `details`).

### Account attributes (2026-10-18)

Slice 5 of "Following TMF" (TMF666), migration `0023`, 375 lines left: recorded with the
accounts, [accounts.md, "Account attributes"](accounts.md#account-attributes-2026-10-18).
`relatedParty` holds parties in any role around one resolved anchor (customer / owner),
the descriptive attributes are slice 4's documents, `paymentStatus` is a record,
`ratingType` `postpaid` or `prepaid` (P5a), `billCycleDay`/`currency` optional.

### Catalog attributes (2026-10-18)

Slice 6 of "Following TMF" (TMF620), migration `0024`, 161 lines left: recorded with the
catalog, [catalog.md, "Catalog attributes"](catalog.md#catalog-attributes-2026-10-18).
The descriptive attributes are slice 4's documents (own-catalog references in them
stored as sent), inline prices, a price's charge and an offering's specification
changeable until the offering is ordered, the code one `externalIdentifier` entry,
reference versions checked, `productSpecification`/`validFor`/`price` optional.

### Order attributes and drafts (2026-10-18)

Slice 8a of "Following TMF" (TMF622), migration `0026`. 105 lines left `tests/tmf-gaps.txt`
(161 -> 105 lines, 156 -> 100 gaps; none for TMF622). The human's answer to the four open
choices was "decide based on TMF"; what TMF defines is implemented, what it leaves open
is ours, as below. `modify` items come in slice 8b (below, when done).

- **Documents.** The order's agreement, channel, note, notificationContact,
  orderRelationship, payment, productOfferingQualification, quote, relatedParty,
  requestedCompletionDate, productOrderMilestone, productOrderJeopardyAlert (POST and
  PATCH) and expectedCompletionDate (PATCH only: ProductOrder_FVO lacks it), and an
  item's appointment, itemTerm, note, payment, productOfferingQualificationItem,
  quoteItem, qualification, productOrderItemRelationship are slice 4's documents
  (`product_order.details`, `product_order_item.details`, checked against `spec/`).
  Processing never reads them; an order's are changed by PATCH (null removes), an
  item's are replaced with the item. A relationship's `id` must be another item of the
  same order (the spec says so; 422).
- **Server-set on an item:** `state`, `itemPrice`, `itemTotalPrice` may be repeated on
  POST (and on a draft's items), compared with the result (slice 1's rule; 422 if they
  differ).
- **The billing account is optional** (TMF622 does not require it): the order's, else
  the first item's `billingAccount`, else the account of the first product a
  delete/noChange item names. An order where none gives one is a 422 (nothing stored);
  billing needs one account per order. Items naming another account are rejected as
  before (`billingAccountMismatch`, `productOfOtherAccount`).
- **noChange** items are executed: the item names a product (`product` required,
  `productOffering` not allowed, like delete) of the order's account that has not ended
  (`productOfOtherAccount`, `productAlreadyEnded`); it completes and changes nothing.
  cancelProductOrder has nothing of it to undo. A product may be named by one item of an
  order only.
- **Nested items** (`productOrderItem.productOrderItem`, any depth) are items like any
  other: processed with the same rules, all or nothing over the tree, ids unique across
  it, answered nested as sent (`product_order_item.parent_id`, in the same order by a
  composite foreign key).
- **Drafts** (`requestedInitialState: draft`; the default `acknowledged` is stored and
  answered on every order): stored without processing - only what the database needs
  is checked (ids, that what it references exists, one account) - with state `draft`
  and items without a state. PATCH changes anything of a draft (its account, items and
  start date replaced whole); PATCH `state: acknowledged` processes it as an order placed
  at that moment (its id, `creationDate` and documents kept; the start date, if none,
  today; completed or rejected, 200). A draft is deleted with DELETE or cancelled with
  cancelProductOrder (`draft -> cancelled`, items `cancelled`, nothing to undo). Once
  processed, the account, items and start date may only be repeated (409 otherwise, was
  a 400 read-only); any other `state` in a PATCH is a 422.
- **States never reached:** pending, held, partial, assessingCancellation,
  pendingCancellation and inProgress.accepted describe asynchronous fulfilment. Orders
  here are processed synchronously and all or nothing, which TMF allows, so they are
  not reached (`app/ordering/states.py`). `failed` is reached only by a dated prepaid status
  change that no longer applies on its date ([prepaid.md](prepaid.md#slice-8-dated-prepaid-status-changes-and-number-only-changes-2026-10-25)).
- **MCP:** the recursive item model is inlined once in the tool schema; below that, a
  nested item is "an object of the same shape" (`bss_mcp/schema.py`).

Tests: `tests/test_api_order_attributes.py` (documents, spec checks, relationships,
server-set items, derived account, noChange, nested items, drafts),
`test_0026_order_details_drafts_and_nested_items`, the updated cases in
`test_api_ordering.py`, `test_api_order_patch.py`, `test_order_states.py`, and the gap
lock. Negative proofs: without the relationship check, without the item server-set
comparison, with drafts processed at once, and without noChange's account check, the
tests above failed. Rolling back `0026` needs `alembic downgrade 0025` by hand; it
fails while a draft exists (delete it first) and flattens nested items.

### Modify items (2026-10-18)

Slice 8b, design approved by the human on 2026-10-18 (billing in
[billing-core.md](billing-core.md#offering-changes-2026-10-18)), migration `0027`. The
human: "decide based on TMF". TMF622 only defines the `modify` action. What it changes
and when is up to us, and decided here. `actionNotSupported` is no longer produced:
every action is executed.

- **Shape.** A modify item names `product` (the product to change, as a delete item
  does) and `productOffering` (the offering it changes to). Both are required: a 422
  otherwise, nothing stored. `requestedStartDate` (today if absent) is the **change day
  D**: the old offering is billed until D-1, the new one from D. A modify item changes
  nothing else.
- **Rejections** (business rules, 201 with the order `rejected`): `productOfOtherAccount`;
  `productAlreadyEnded` (the product has ended, or its known termination is on or
  before D); `productNotStarted` (D before the product's start date; D on the start
  date replaces the initial offering, with nothing billed for it); `openModifyItem`
  (the product already has a change that has not taken effect, or a change on D);
  `terminationInPeriod` (2026-10-19, below: the product's known termination falls
  before the end of D's billing period);
  `offeringUnchanged` (the product is already on that offering on D; that is what
  noChange is for); plus add's checks on the new offering at D: `offeringNotSellable`,
  `offeringNotValid`, `noRecurringPrice`, `priceEnds`. **A delete gains
  `openModifyItem`**: it may not end a product on or before the day of a change that
  has not taken effect (cancel that order first). These rules keep "one refund per
  charge" true.
- **Execution.** Inserts the `product_offering_change` row. The product's status does
  not change. The item references the product, and its quote (`itemPrice`) is the new
  offering's recurring price at D, as an add item's is (the `product_order_item_quote`
  check now allows it on modify items; a new check requires a modify item that was not
  rejected to name its product and offering). `ON_PRODUCT` includes `modify`, and
  `_item_request` keeps a modify item's product. A product may still be named by one
  item of an order only.
- **TMF637.** `productOffering` (and `name`) is the offering valid today (on the start
  date for a product that has not started). `productPrice` is the price of the segment
  that bills today. `productOrderItem[]` lists each modify item with role
  `modification`, between creation and termination. The internal open-products view
  uses the same offering; the first-charge and price-change previews concern an offering,
  not a product, and are unchanged.
- **Events.** ProductAttributeValueChangeEvent: at order time when D is today, else from
  the worker's run of D (at the status flips, with their visibility).
  `product_offering_change.effect_announced_at` records it, so a retried run emits it
  once. The order's own events are as before.
- **cancelProductOrder** undoes a modify item before it takes effect (D after today) by
  deleting its change row (ProductAttributeValueChangeEvent: its `productOrderItem[]`
  changed). It is refused (409, nothing stored) once billing has reflected the change:
  any ABR of the product starting on or after D, or a charge ending on D when D is not a
  cycle day (the reduced charge of the old offering). A full charge of the old offering
  that has not been refunded yet does not block it. Cancelling an add order is refused
  while another order changes its product's offering.
- **Catalog.** An offering named by a change counts as ordered, which already holds
  because the modify item names it; the same reference makes deleting it a 409.
- **Not in this slice:** a "change offering" action in the UI, and the seed. The probe
  sees no gap here (`modify` was already accepted by the schema), so `tests/tmf-gaps.txt`
  is unchanged (100 gaps). Tests that used `modify` as a quick rejection now use
  `apidata.rejected_item` (an add item naming another account: `billingAccountMismatch`);
  `test_full_flow.py` modifies its ended product (`productAlreadyEnded`).

Tests: `tests/test_api_order_modify.py` (the shape 422s, every rejection, the delete
rule, a change placed on a day first seen by the next day's run, billing through the
API - a mid-period change after the month was charged and one known beforehand -
TMF637 and the TMF678 product names before and after D, the open-products view, events
from the order and from the worker, cancellation allowed and refused);
`test_0027_offering_changes`. Negative proofs: without the delete rule, without the
cancellation's billing check, and with the worker announcing a change again on a retried
run, the tests above failed.

### The last gaps (2026-10-18)

**Implemented 2026-10-19** (the human confirmed the plan below as written, including
"billing does not change" for suspensions); migration `0028`. `tests/tmf-gaps.txt` has
**no open gap**; its `# accepted deviations` section holds six lines, each with its
reason. What the implementation decided beyond the plan:

- **Step 0.** Nested response attributes are probed on populated data (the demo seed
  plus one of each resource it does not create, `tmf_gaps.populate`): an attribute of an
  object/array a response carries is *missing* when no returned value has it - unless
  POST stores it as sent (an unset optional attribute is no gap). 38 new lines, so no
  split. Two negative controls lock the probe (a defined-but-refused attribute, a
  defined-but-never-returned nested one).
- **Four more accepted deviations** (no value here, rather than an invented one):
  `accountBalance.validFor` (the balance now, no period) and `billDocument.href`, `url`,
  `validFor` (the document is embedded as `content`: no address of its own, no validity).
  `accountBalance.id` is `currentBalance-<currency>`; a bill document's `id` is its
  bill's, `size` its byte length, `description` names the bill and its format.
- **billCycle is a daily run**, not an account's cycle: `BillCycleSpecification` is the
  built-in monthly policy every run applies (named specifications are names of it),
  `chargeDate` = `mailingDate` = `creditDate` = the run date, `paymentDueDate` = the due
  date of the bills it issued on its date (else run date + `BILL_PAYMENT_TERM_DAYS`).
- **Prices in TMF622/637:** `priceAlteration` is always `[]` (billing applies none);
  `unitOfMeasure` is the catalog price's Quantity as text (TMF622/637 type it a string);
  `orderTotalPrice` names the order's account and, when every add item has the same
  price, that price and unit. Every `BillingAccountRef` states the account's `ratingType`.
- **TMF676:** `paymentMethod` is stored as sent (validated against
  `PaymentMethodRefOrValue`) and answered as sent; the model still reads its name and
  @type. `account.description` is stored beside the reference.
- **TMF637 PATCH:** one order per PATCH (terminationDate, productOffering or status;
  422 otherwise); name and documents change directly with a
  ProductAttributeValueChangeEvent; repeating `billingAccount`/`startDate` is a 200
  no-op. **POST terminationDate** must be after the start (422); a future product
  accepts a later scheduled termination (review C, 2026-10-19, human approved).
- **The product's name is its own:** after an offering change it keeps the name it was
  created with (until PATCHed); `productOffering.name` names the offering.

**Plan, prepared for the next session; the human confirms it before implementation.**
The human asked to close every remaining gap in one slice (replacing slices 9 and 10)
and left each open choice to "TMF" ("follow TMF recommendations", "decide based on
TMF"). The rule ([Following TMF](#following-tmf-2026-10-17)) decides them: what a spec
defines is implemented; behaviour it leaves undefined is not invented; our own
requirements stay only where billing cannot work without them, with the reason
recorded. Starting point: `tests/tmf-gaps.txt` has 100 gaps (TMF637 49, TMF676 39,
TMF678 12), plus the behaviour gaps of [the gap analysis](../tmf-gap-analysis.md).

**Step 0 - nested response attributes** (the human: "decide based on TMF": the specs
define them, so they are in). Extend `tests/tmf_gaps.py` one level into responses
(the attributes of each object/array attribute of every resource schema, through
`fields=` where it can address them, else from a full GET of a seeded resource), lock
the new lines in `tests/tmf-gaps.txt`, and close them in this slice. If they come to
more than about 150 lines, split by API (9a: TMF637/TMF676/TMF678 below, 9b: the nested
lines) and say so before going on.

**TMF637 (49 lines).**

- **Descriptive attributes** (POST and PATCH; slice 4's documents, checked against
  `spec/`, stored in a new `product.details`): `agreementItem`, `intent`, `place`,
  `productCharacteristic`, `productRelationship`, `productTerm`, `realizingResource`,
  `realizingService`, `product` (a bundle's member products as references or values -
  records, no inventory of their own); plain columns or document keys: `description`,
  `productSerialNumber`, `isBundle`, `isCustomerVisible`. PATCH changes them directly
  (they neither create nor end a product, so "orders alone create or end products"
  holds); ProductAttributeValueChangeEvent.
- **`name`** becomes the product's own (POST and PATCH; default: the offering's name at
  creation, as today's answer).
- **PATCH `productOffering`** places a modify order effective today (slice 8b's rules;
  a rejection is a 409, nothing stored, as product writes do). **PATCH `status`**
  `suspended` / `active` places the suspension or resumption order below. **PATCH
  `billingAccount`, `startDate`** may only repeat the stored value (409 otherwise,
  naming the way out: a product's account never changes - billing; a start date moves
  by cancelling the order and placing a new one) - slice 8a's pattern for processed
  orders.
- **POST `terminationDate`** places the add order and a delete order with that date in
  the same transaction (two ordinary orders); **POST `name`** as above; **POST
  `productOffering.version`** checked like slice 6's reference versions.
- **POST requires `billingAccount`, `productOffering`: kept as recorded deviations**
  (billing cannot charge a product without an account and an offering; the rule allows
  exactly this). `tests/tmf-gaps.txt` gets a section `# accepted deviations` the probe
  output is compared with as well, holding these two lines with their reason; the lock
  stays exact.
- **Suspension** (the human: "follow TMF recommendations"). TMF622 changes a product's
  state with a `modify` item carrying `product.status` - TMF637 has no suspend
  operation. So: a modify item with `product.status: suspended` (from `active`) or
  `active` (from `suspended`) and no `productOffering`, effective `requestedStartDate`;
  `pendingSuspend`-like statuses do not exist in the spec, so a future suspension leaves
  the status as it is until the worker's run of the date flips it (as `created` ->
  `active` and `pendingTerminate` -> `terminated`); cancelProductOrder undoes it before
  the date. **Billing does not change**: no spec defines what a suspension does to
  charges, so a suspended product is billed by its dates as today (the open question in
  billing-core.md is closed that way; a business rule to stop charges would be ours and
  needs the human). One item changes either the offering or the status, not both.
- **C9**: a delete or a suspension/resumption order on a `Suspended`/`Closed` billing
  account is executed (TMF666's account states forbid nothing); add and offering
  changes still need an `Active` account (`accountNotActive`).
- **`pendingActive`, `aborted`**: describe asynchronous provisioning; orders here
  complete synchronously, so never reached - recorded, like the order states of slice
  8a.

**TMF676 (39 lines).** `name`, `description`, `authorizationCode`, `correlatorId`
(text), `channel`, `payer` (payment), `requestor` (refund) as documents; `account` and
`paymentMethod` keep the extra attributes sent (`account.description`,
`paymentMethod.description`, `isPreferred`, `relatedParty`, `status`, `statusDate`,
`validFor`, `account`) as stored documents beside the references they resolve to.
**Payment states**: TMF676 v4 `status` is free text with no states or transitions, so
there is nothing to implement beyond what exists (`done`); recorded, and the "payment
states other than done" gap is closed as "not defined by the spec".

**TMF678 (12 lines).**

- appliedCustomerBillingRate: `name` = the ABR's offering name, `description` = what
  it bills (`<offering>, <period>`; a refund: "refund of ..."), `characteristic` = `[]`
  (billing sets none; the attribute is answered).
- billCycle: `BillCycleSpecification` = the account's cycle specification reference
  (or the built-in `monthly`), `chargeDate` = `mailingDate` = `creditDate` = the bill
  date (the run charges, issues and applies credit on the cycle day), `paymentDueDate`
  = bill date + `BILL_PAYMENT_TERM_DAYS` (the term in force).
- customerBill: `financialAccount` (the billing account's, if linked), `paymentMethod`
  (the account's default payment method document, if any), `relatedParty` (the
  account's customer, role `customer`).
- customerBillOnDemand: `relatedParty` accepted on POST (document) and answered.

**priceType `discount`, `allowance`, `penalty`** (the human: "decide based on TMF"):
TMF620 names them only as examples in a free-text description, with no arithmetic, so
they are accepted on prices as records (stored, served, imported/exported) and never
billed; billing keeps using `recurring`. Recorded in catalog.md.

**Also:** the gap analysis' "party `status`" item is stale (writable since slice 4) and
is removed; every behaviour gap of its list ends closed or recorded as not defined by
the specs. Migration `0028` (product details/name, payment and refund documents,
on-demand relatedParty, suspension items). Tests per area, negative proofs, `make
progress`, API notes for TMF637/676/678 (harness files: diff first, the human's OK).

### Suspension (2026-10-19)

The human: "follow TMF recommendations"; confirmed with the plan above, billing
unchanged. TMF637 has no suspend operation; TMF622 changes a product's state with a
`modify` item carrying `product.status`. Code: `app/ordering/process.py`
(`_status_problems`), `app/repository/products.py` (status changes), the worker's flip
step. Tests: `tests/test_api_suspension.py`.

- **The item.** `action: modify`, `product: {id, status}` with `status` `suspended` or
  `active` (resume), no `productOffering`: an item changes the offering or the status,
  never both (422). `product.status` on another action is a 422. Effective
  `requestedStartDate` (today if absent). Stored in `product_order_item.product_status`
  and answered in the item's `product`.
- **The change** is a `product_status_change` row (product, day, status, item). Today:
  `product.status` is set at once (ProductStateChangeEvent). Later: the status stays as
  it is - the spec has no pending suspension state - until the worker's run of the day
  sets it, after the created -> active flips (a product suspended from its start day is
  activated first), with its event, once.
- **Rules** (rejections, the order stored as `rejected`): the product is the account's
  (`productOfOtherAccount`); it is not ended and has no end set (`productAlreadyEnded`:
  an end decides the status - pendingTerminate, then terminated; cancel that order
  first); the day is on or after its start (`productNotStarted`); one status change waits
  at a time and they come in date order, one per day (`openModifyItem`, as offering
  changes); it changes something (`statusUnchanged`: suspend an active product, resume a
  suspended one). A delete item waits while a status change is pending
  (`openModifyItem`): end the product once the change took effect, or cancel it.
- **Ending a suspended product** is an ordinary delete: `terminated`, or
  `pendingTerminate` until its date (the coming end wins over `suspended` in the status).
  Cancelling that delete gives the product back the status its changes left it in
  (`suspended`).
- **cancelProductOrder** undoes a future status change (the row is deleted, no event)
  or, since review B (2026-10-19, human approved), one dated today even after it took
  effect. An effective change's row is deleted, the latest applied status restored
  (default active), and ProductStateChangeEvent emitted. Future changes remain scheduled;
  an older change is a 409. If another order set an end, cancel that order first.
  An add item cannot be undone once another order changed the product's status.
  Negative proof (review B): restoring the old date/applied check makes both same-day
  undo tests fail with 409; reverted. Worker-applied changes are covered too.
- **Billing does not change**: no spec defines what a suspension does to charges, so a
  suspended product is billed by its dates (decisions/billing-core.md, open question
  closed). A rule that stops charges would be ours and needs the human.
- **C9**: adding a product or changing its offering needs an `Active` billing account
  (`accountNotActive`); delete, suspend, resume and noChange items are executed on a
  `Suspended` or `Closed` one too (TMF666's account states forbid nothing).
- **TMF637 PATCH `status`** `suspended`/`active` places such an order effective today
  (409 with the reasons if it would be rejected); any other status is a 422 (a product is
  created by an order, ended by terminationDate or DELETE).
- `pendingActive` and `aborted` describe asynchronous provisioning: orders complete
  synchronously here, so they are never reached (recorded, like the order states of
  slice 8a).

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
  **Superseded 2026-10-18** ([base paths](#base-paths-2026-10-18)): TMF629 is
  `customer/v5`, TMF676 `payment/v4`.
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
- **Narrowed 2026-10-17** ([above](#nothing-dropped-or-rewritten-2026-10-17)): a POST
  may choose the `id`, `@baseType`/`@schemaLocation` are stored, and server-set
  attributes (`lastUpdate` on PATCH among them) may be repeated, never set (422).
- **`@referredType` in a reference is checked, never rewritten** (`cloud/quality-1`,
  earlier finding #17). The vendored specs define it as "the actual type of the target
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
  `X-Total-Count` = all matching rows, `X-Result-Count` = rows in this response. (Since
  #312: no `X-Total-Count`; `Link: rel="next"` while more rows follow, see below.)
  `fields=` selects top-level attributes (`id`, `href`, `@type` always); an unknown name
  is a 400. Filters are equality only, on the attributes listed per resource; any other
  query parameter is a 400.
- **POST** returns 201 with `Location`. **PATCH** accepts `application/merge-patch+json`
  only (415 otherwise); `null` removes an optional attribute and is a 422 on a mandatory
  one; `@type` may be repeated but not changed.
- **Superseded 2026-10-17** ([PATCH formats](#patch-formats-2026-10-17)): PATCH accepts
  the four formats the specs list; 415 only for anything else.
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

| payment | `billing_account_id` <-> `account` (AccountRef, `@referredType: BillingAccount`), `amount` + `currency` <-> `totalAmount` and `amount` (equal: no taxes), `received_at` <-> `paymentDate` and `statusDate` (the clock at POST), `status` (`done`), `payment_method` <-> `paymentMethod` (`{name, @type}`, both the stored word; since `0021` each as sent) | `account.id`, `status` |
|---|---|---|

POST records a received payment: `account`, `totalAmount` and `paymentMethod` (with a
`name` or an `@type`) are mandatory, as in the spec. `amount`, if sent, must equal
`totalAmount`; `taxAmount`, if sent, must be zero. **Since 2026-10-18 (taxes)**:
`amount` is net and `taxAmount` the tax of `totalAmount`; either gives the other, both
must add up, `tax_amount` is stored and both are answered
([billing-core.md](billing-core.md#taxes-2026-10-18)). `totalAmount.unit` must be the
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

**Since 2026-10-18 (taxes)** an item's `taxAmount` is no longer zero-only: the item
allocates `amount + taxAmount`, which `totalAmount` (if sent) must equal
([billing-core.md](billing-core.md#taxes-2026-10-18)).

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
  **delete of a product that has not started** cancels it when dated on or before its
  start: status `cancelled`, `termination_date = start_date` (0 billable days); a
  requested date after its start schedules termination: `created` until start, then
  `pendingTerminate`, then `terminated`. A worker catching up past both dates moves
  straight to `terminated`, respecting the terminating order's visibility cutoff.
  Cancelling that delete restores `created` before start or the applied status after
  start, subject to the existing billing checks. An add order with such a delete
  cannot be cancelled until that delete is undone. Modify and status rules with an
  end set remain as before (including `terminationInPeriod`). Tests:
  `tests/test_api_product_writes.py::test_future_delete_dates`,
  `::test_post_future_product_with_end_and_cancel`.
  Negative proof (review C): restoring unconditional cancellation of unstarted products
  makes the after-start delete case fail (`cancelled` instead of `created`); reverted.

## List paging and filters (2026-11-09, #286)

- **Superseded by #312 (2026-11-10): no counts.** ~~`X-Total-Count` stays exact~~ (TMF630; the UI requires it). It is kept cheap by indexes,
  and for productUsage by requiring a line (docs/decisions/usage.md), not by estimates.
- **Offset paging stays** (`offset` / `limit`). Every list pages `ORDER BY created_at, id`,
  served by a `(created_at, id)` index on each table that grows with subscribers
  (migration 0070). The catalog stays small and has none.
- **Typed filters.** `select_page` compares each value with the column's own type, so the
  filter indexes apply. A string for an enum column is checked against the enum's labels
  (loaded once per process). One that is no label answers an empty page with
  no rows and no query, as before ("an unknown value matches nothing").
- **Filter indexes by rule:** a reference (`*_id`) filter without an index gets a btree, as
  does a name filter; status/state filters get none (low selectivity). Each list's filters
  and the index serving them are listed in the PR of #286.

## Batched order bodies (2026-11-13, #299)

`productOrder` loads each related table once for the page: accounts, items, error
messages, offerings, quoted prices and prepaid commands. The single-order builder
uses the same loader with a page of one, including write responses and event bodies.
Items retain `ORDER BY position`; error messages retain
`ORDER BY product_order_item_id NULLS FIRST, occurred_at, code, id`.
Prepaid tax quotes still come from the command's stored intervals, falling back to
the quoted catalog price as before. No response attribute or nested array order changes.

`cancelProductOrder` needs no related rows; its existing row builder already serves
both list and single-item responses. `tests/test_list_queries.py` pins the statement
counts for full pages and compares list bodies with creation and single-item responses.

## Batched payment, saved-card and usage bodies (2026-11-13, #301)

`payment` loads accounts and allocations once per page. Allocations remain summed
per payment and bill, in `ORDER BY b.bill_date`, with `paymentItem` absent when
nothing was allocated. `paymentMethod` loads accounts once per page. `productUsage`
loads products and the rated records' applied prices once per page; unrated records
still omit `usageProductPrice`, and rated records without a price still return `[]`.
Single-item reads, write responses and event bodies use the same loaders with one row.
No response attributes or nested array order change; no migration is needed.

`tests/test_list_queries.py` compares pages of 1 and 100 through the raw client:
payment uses 7 statements, saved cards 6, and usage 6 (unrated) or 7 (rated), including
session authentication and pool checks. Full pages are compared with single-item
responses, and payments/cards also with their creation responses.

## Batched bill, rate and cycle bodies (2026-11-13, #300)

`customerBill` loads accounts, linked financial accounts, customers and billing runs
once per page. One allocation query supplies both the total (including credit-note
allocations) and payment groups, retaining `ORDER BY min(allocated_at), payment_id`.
One ABR query aggregates net amounts separately from expanded tax lines, so multiple
taxes do not multiply the net; tax items retain category/rate ordering. Bill documents
and the daily-run-only bill-cycle link are unchanged.

`appliedCustomerBillingRate` batches accounts and offerings, retaining the no-product
transfer-fee fallback. `billCycle` batches stored due dates, still considering only
bills whose bill date equals their run's date and falling back to the configured term
only when none exists. Every single-item builder uses the same loader with one row.

`tests/test_list_queries.py` pins equal statement counts for pages of 1 and 100:
11 for bills with financial links, 7 for rates and 6 for cycles, including session
authentication and pool checks. It covers full pages, multiple products/tax lines and
payments, empty pages, stored versus fallback terms, and single-item response equality.
No contract change or migration is needed.

## Batched message, offering, account and on-demand bodies (2026-11-14, #303)

`productOffering` loads specifications, prices (`ORDER BY created_at, id`) and category
memberships (`ORDER BY m.created_at, t.id`, one join for the page) once per page.
`financialAccount` loads owners, linked accounts (one union, creation order) and
balances (the linked billing accounts, then `account_balances` for all of them) once per
page; balances are still summed per currency in currency order. `partyAccount` and
`settlementAccount` load owners and financial accounts once per page.
`customerBillOnDemand` loads billing accounts once per page. `communicationMessage`
needs no related rows. Single-item reads, write responses and event bodies use the
same loaders with one row; no response attribute or nested array order changes.

`tests/test_list_queries.py` pins equal statement counts for pages of 1 and 100 and
compares full pages with single-item reads (and creation responses where nothing
changed since).

## Lists without counts (2026-11-10, #312)

Decided by the human: no list counts its rows. A deliberate deviation from TMF630's
`X-Total-Count`: at 100k subscribers an exact count re-reads the matching rows on every
page request, and grows with every month of history.

- **Every list** (TMF, internal, MCP) reads `limit + 1` rows and answers `limit`, with
  `X-Result-Count` and, while more rows follow, `Link: <the same request, offset +
  limit>; rel="next"` (RFC 8288). No response carries `X-Total-Count`.
- **Every list takes `offset` / `limit`** (default 20, maximum 100). The lists that used
  to return everything now page as well: an account's open products, a product's
  devices and port PINs, SIM batches, users.
- **productOrder and cancelProductOrder are listed newest first**
  (`ORDER BY created_at DESC, id DESC`, decided by the human). TMF622 does not specify an
  order, and without a count the UI could no longer read them from the end.
- **So are the other event lists** (decided by the human 2026-11-11, #314): customerBill,
  payment and refund (`created_at DESC, id DESC`, the 0070 indexes read backwards), and
  each TMF654 action kind, topupBalance, adjustBalance, reserveBalance and
  transferBalance (`requested_at DESC, id DESC`, index of migration 0074). The prepaid
  balance history (every kind together) and every other list stay oldest first.
- **The UI** pages with Previous/Next and shows no "of N". **MCP** list tools return
  `resultCount` and `hasMore`.
- **Screens** that still fetch a whole list of a growing table are a follow-up issue
  (#312's decision 3; see that issue).
