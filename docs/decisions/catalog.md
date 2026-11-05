# Catalog: TMF620 mapping and price rules

Since 2026-10-20, recurring prices support daily, weekly, monthly and yearly units
with lengths 1..1000; see [charge-periods.md](charge-periods.md). This extends the
monthly-only mapping recorded below. Successor prices retain the same interval.

Index: [README.md](README.md). Shared API
conventions: [tmf-api.md](tmf-api.md).

## TMF620 mapping

### Alteration price types (2026-10-19)

Part of [the last gaps](tmf-api.md#the-last-gaps-2026-10-18) (the human: "decide based
on TMF"). TMF620's `priceType` is free text whose description names `recurring`,
`discount`, `allowance`, `penalty` as examples, with no arithmetic. So a price may be
`discount`, `allowance` or `penalty` besides `recurring` and `oneTime`: stored, served,
exported and imported like any price - **a record, never billed**. Billing charges
`recurring` prices only, as before; TMF622/637 prices answer `priceAlteration: []`.
Like `oneTime`, an alteration has no `recurringChargePeriodType` (422). Migration
`0028` adds the three values to `price_type` (rolling back refuses while such a price
exists). Test: `tests/test_api_last_gaps.py`.

### Catalog attributes (2026-10-18)

Slice 6 of "Following TMF" ([tmf-api.md](tmf-api.md#following-tmf-2026-10-17)), the plan
approved by the human (2026-10-18, "go"; the two open questions "decided on TMF
standards"). Migration `0024`. 214 lines left `tests/tmf-gaps.txt` (375 -> 161): every
attribute TMF620 defines for specifications, offerings, prices and catalogs is taken on
POST and PATCH and returned (`fields=` included). TMF620 has no gap lines left.

- **Documents** (slice 4's mechanism: `details` jsonb on `product_specification`,
  `product_offering`, `product_offering_price`, `product_catalog`; checked against the
  `_FVO`/`_MVO` schema at every depth; a merge patch replaces one whole, `null` removes
  it). A specification's brand, productNumber, isBundle, version, validFor, relatedParty,
  characteristics, service/resource specifications, bundled specifications and
  relationships, attachment, policy, targetProductSchema, intentSpecification,
  externalIdentifier and category; an offering's version, statusReason, isBundle,
  place, channel, marketSegment, SLA, service/resource candidate, productOfferingTerm,
  agreement, bundled (group) offerings, relationships, characteristics,
  prodSpecCharValueUse, attachment, policy, allowedAction; a price's description,
  version, externalIdentifier, unitOfMeasure, isBundle, percentage, popRelationship,
  bundledPopRelationship, prodSpecCharValueUse, productOfferingTerm, place, policy,
  pricingLogicAlgorithm and tax; a catalog's relatedParty. **Records only:** ordering
  and billing ignore bundles, terms and `percentage`/`popRelationship` (discounts).
  **`tax` is applied by billing since slice 7** and is part of a price's charge below
  ([billing-core.md](billing-core.md#taxes-2026-10-18)). The lists are
  locked to the spec schemas (`test_every_spec_attribute_has_a_place`).
- **References to our own catalog entries inside documents are stored as sent, not
  resolved** (question 1, decided by TMF: the spec defines their shape only, and its
  own examples - `bundledProductSpecification` ids `15`, `64` - must round-trip):
  bundled and related specifications and offerings, a specification's `category`,
  `popRelationship`, `bundledPopRelationship`. Nothing makes a DELETE search jsonb.
- **Optional, as in the spec:** an offering's `productSpecification` (its products then
  have none; the internal products view answers `productSpecification: null`) and
  `validFor` (absent: orderable at any time; `valid_for` NULL); a price's `validFor`
  (absent: from now on) and `price` (`amount`/`currency` NULL together). **A price
  without an amount prices no offering**: attaching one is a 409, an inline one is a
  422, and a CHECK (`product_offering_id IS NULL OR amount IS NOT NULL`) keeps billing
  from ever seeing one. An offering's validFor removed by `null` counts as a start
  moved into the past (409) unless it had no start before.
- **A price's charge** (`price`, `priceType`, `recurringChargePeriodType`,
  `recurringChargePeriodLength`, and since slice 7 `tax`) **changes on PATCH while the price has priced
  nothing** - unattached, or its offering never ordered (rejected orders count), the
  condition of DELETE (question 2, decided by TMF: the spec lets a client PATCH it; we
  refuse only where billing history is at stake). Otherwise a 409 "end its validFor
  and attach a new price"; repeating the current charge is no change. Monthly with
  length 1 stays the only period (billing needs it). Retires the read-only charge of
  "Specifications, offerings and prices" below.
- **An offering's `productSpecification` changes on PATCH until the offering is
  ordered** (409 after: a product's productSpecification is its offering's); `null`
  removes it. **Its `category[]` is writable on POST and PATCH** - the same membership
  the category's `productOffering[]` writes, under the `(620, 9)` lock taken before
  the offering row (as DELETE takes them); duplicates and unknown categories are a 422.
- **`externalIdentifier` is a list of any identifiers** on offerings, specifications
  and prices. On an offering, the entry with `externalIdentifierType: offeringCode` (at
  most one; owner `bss`, filled in when absent; the old pattern) is the code: unique
  (409), changeable on PATCH (nothing in billing reads it), the `externalIdentifier.id`
  filter and the UI's code. The other entries are stored as sent. `@type` is required in
  each entry, as ExternalIdentifier_FVO says. Migration `0024` copies existing codes
  into the list as the entry they were answered as.
- **Inline prices** (`ProductOfferingPriceRefOrValue`): an entry of an offering's
  `productOfferingPrice[]` with `@type: ProductOfferingPrice`, or with neither `@type`
  nor `id`, is a new price - created under price POST's rules (never in the past, an
  amount required), attached in the same transaction, with its own
  `ProductOfferingPriceCreateEvent` (an import emits only its job's). An entry with
  `@type: ProductOfferingPriceRef`, or with an `id` and no `@type`, is a reference, as
  before. Problems are reported under `productOfferingPrice.<index>`. Retires the
  "only references" deviation below. TMF620's own offering example types its price
  reference `ProductOfferingPrice`, which its discriminator maps to a value: the schema
  wins, the example is sent as a reference in the tests.
- **A reference's `version`** (category `parent`/`productOffering`, catalog `category`,
  an offering's `productSpecification` with its `targetProductSchema`, a price
  reference) must be the referred entry's current one (422 otherwise; no versions are
  kept). References in responses carry the version when the entry has one.
- **The spec's examples disagree with its schemas in places** (an AttachmentRef with
  `mimeType`, a number characteristic value `"8"`, a ProductSpecificationRelationship
  `role`, a PricingLogicAlgorithm `href`, a ProductOfferingPriceRelationship `validFor`):
  the schemas win, as everywhere; the tests send the examples corrected
  (`tests/test_api_catalog_attributes.py`, `example`).
- UI: the code is the `offeringCode` entry (not the first identifier); an offering
  without validFor shows "any time"; a product without a specification shows its code
  alone. MCP: the request models carry the documents (`documents_model`).

Tests: `tests/test_api_catalog_attributes.py` (the spec's three create examples round
trip, nested problems, documents patched and removed, versions, inline prices and their
event, prices without an amount, the charge rule, the specification rule, an offering
without validFor/specification ordered and shown, categories from the offering side, a
catalog's relatedParty), `test_0024_catalog_details_and_optional_attributes`, the
updated code and catalog cases, and the gap lock. Negative proofs: without the
"ordered" check the charge test fails; without the inline amount check the inline test
fails (the CHECK still stops the attach, as a 409 instead of the 422); with the version
check a no-op the version test fails. Rolling back `0024` needs `alembic downgrade
0023` by hand; it fails while an offering has no specification or validFor, or a price
no amount (delete or complete them first).

### Import and export jobs (2026-10-16)

`importJob` and `exportJob` (list, retrieve, create, delete: eight operations) move the
catalog as one JSON document, a `BssCatalogDocument` (version 1) holding the full TMF620
bodies of specifications, prices, offerings, categories and catalogs, in import order.
Migration `0015` adds `catalog_job` (one table, `kind` = `ImportJob`/`ExportJob`).

- **Inline only, no outbound traffic.** The document travels as a
  `data:application/json;base64,...` URL. An export's `url` is the document; an
  import's `url` must be one (anything else is a 422 - the server fetches nothing). The
  spec's `ExportJob_FVO` requires `url` (the file to write): we accept it only as
  `data:application/json`, or not at all. Changed from the approved plan, which had an
  internal download endpoint: the required `url` made the inline form the honest one,
  and an export can be handed to an import (here or on another instance) as is.
  Documents are capped at 1 MB (a larger export is stored `Failed`).
- **Synchronous.** Both run in their request and answer 201 with a finished job:
  `Succeeded` or `Failed` (the JobStateType values `Not Started`/`Running` never occur).
  `status`, `creationDate`, `completionDate` and `errorLog` are server-managed (400 on
  POST). `errorLog` holds the reason as text (the spec describes a path to a log; ours is
  the log). `path` is the TMF620 base path (another value is a 422); `contentType` only
  `application/json`.
- **Export scope:** `query` empty (everything, unattached prices included) or
  `productCatalog.id=<id>`: that catalog, its root categories and their descendants, the
  offerings in them, and those offerings' specifications and prices.
- **Import under POST's rules, keeping the source's ids, all or nothing** (a savepoint).
  The same code paths as POST (`insert_specification`, `insert_price`,
  `insert_offering`, `save`): references must exist, a price never starts in the past
  (so a catalog whose prices already took effect cannot be imported - approved, strict),
  codes are unique, categories nest without cycles, catalogs take roots only. An entry
  whose id exists is compared with the document (ignoring `lastUpdate` and derived
  navigation: an offering's `category[]`, a category's `subCategory[]`): equal is
  skipped, so re-importing is a no-op; different fails the job, naming the attributes.
  A failed import is stored with its `errorLog` and writes nothing, like a rejected order.
- **Deleting a job** removes the record only; nothing it imported is undone.

MCP gets eight tools (`create_export_job` -> `create_import_job` round trip; the job
writes need the write scope). Coverage: 92/129 non-listener operations (71.3%); TMF620
now lacks only `/hub`. Tests: `tests/test_api_catalog_jobs.py`,
`tests/test_migrations.py::test_0015_catalog_jobs_round_trip`. Negative proofs: without
the savepoint a failed import left its specification behind; without the comparison a
conflicting entry was skipped and the import succeeded; without the past-price rule the
import failed only later, at the offering's attach check - each test failed and the code
was restored.

### Deleting catalog entries (2026-10-16)

D1 ("DELETE anywhere") is narrowed to the catalog. `DELETE` on `productSpecification`,
`productOffering`, `productOfferingPrice`, `category` and `productCatalog` answers 204
(no body, no query parameters - any is a 400) when nothing references the entry, and a
409 naming the references otherwise, with nothing changed:

| Entry | 409 while | Removed with it |
|---|---|---|
| specification | an offering uses it | - |
| offering | it was ever ordered (a product or any order item, rejected ones included) - retire it instead; or it still has prices - delete them first | its category memberships |
| price | it is attached to an offering that was ever ordered - end its validFor instead | - |
| category | it has subcategories or offerings, or a catalog lists it | - |
| catalog | never (nothing refers to a catalog) | its root-category memberships; the categories stay |

**Why this is safe for billing:** billing reads an offering's prices by date, not through
a foreign key, so the rule is about the offering: once anything ordered it, the offering
and all of its prices stay. An unattached price, or one on a never-ordered offering, has
priced nothing. The foreign keys to the catalog (all `NO ACTION`) back the checks: a
reference the checks missed is a 409 "still referenced", never a dangling row.

**Concurrency.** Orders take each offering `FOR SHARE` before validating (formerly a
plain read), offering POST takes its specification `FOR SHARE`, offering PATCH takes the
offering `FOR UPDATE`; deletes take the row `FOR UPDATE` (a price: its offering's row,
as price changes do), category/catalog/offering deletes also the advisory lock
`(620, 9)`. So a delete either waits for an order in flight and then sees it (409), or
goes first and the order sees the catalog without the entry (rejected `noRecurringPrice`,
or 422 for a missing offering/specification). Without the order's lock, an order could
validate a price chain that a concurrent delete shortens - the reason for the lock.
Concurrent orders still run side by side (shared locks).

MCP gets five write-scope tools (`delete_specification`, `delete_offering`,
`delete_price`, `delete_category`, `delete_catalog`). Coverage: 84/129 non-listener
operations (65.1%). Tests: `tests/test_api_catalog_delete.py`. Negative proofs: without
the order's `FOR SHARE` lock the racing order is a 500 instead of a rejection; without
the specification lock the racing offering POST is a 500 instead of a 422; without the
price's reference check the price of an offering with a rejected order is deleted. All
three tests failed and the protections were restored.

### Categories and product catalogs (2026-10-16)

Eight operations add list/retrieve/create/merge-patch on `category` and
`productCatalog`. Migration `0009` adds `category`, `product_catalog`,
`catalog_category`, and `offering_category`; existing offerings remain unclassified.
No DELETE, import/export or UI management screen is added in this slice.

Both resources support name (required), description, version, optional validFor,
and lifecycleStatus (default In Design, then the existing forward-only lifecycle).
lastUpdate is server-managed, accepted and ignored on create and read-only on PATCH.
Optional description/version/validFor/catalogType may be removed with null.
Validity here is organizational metadata: it does not change sellability or billing.

A category has at most one parent. `parent` is writable, null removes it;
`subCategory` is derived and read-only. `isRoot` is derived from the absence of a
parent; an input value must agree with the resulting parent. Cycles are a 409.
Catalog `category[]` contains root categories only (409 otherwise). A root referenced
by a catalog cannot acquire a parent until removed from all catalogs.

Category `productOffering[]` replaces its offering membership atomically; offerings
may belong to multiple categories. The reciprocal `ProductOffering.category[]` is
read-only; edit membership through the category. Catalog `category[]` also replaces
membership. Empty arrays clear membership; PATCH null arrays are a 422. References
must exist and have the expected referred type; duplicates are a 422. Stored target
names/hrefs are returned. Reference arrays have stable insertion-time/id ordering.

All category and catalog writes take transaction advisory lock `(620, 9)` before
reading state, so concurrent reparenting cannot create cycles or invalidate catalog
roots. Each write and its memberships use one transaction. The new foreign keys,
membership primary keys and direct self-parent check also protect stored relations.
List equality filters: name/lifecycleStatus on both, parent.id on categories,
catalogType on catalogs. Standard pagination, fields and strict queries apply.
Unsupported optional attributes are rejected rather than discarded.

This adds eight non-listener spec operations: 53/129 (41.1%) implemented across the
eight vendored APIs, an operation count rather than a conformance certification.

Validation covers schema-checked navigation, replacement without disturbing other
memberships, missing/duplicate/wrong-type references, fields/queries, lifecycle,
invalid validity periods, concurrent reparenting, and migration upgrade/downgrade
while retaining legacy catalog data. Negative proofs removed the cycle guard and
advisory lock; both corresponding tests failed and the protections were restored.
The local development database has migrated to `0009`; the browser smoke passed.
Final backend validation passed 896 tests in each of the CI and dev profiles
(an earlier full pass had 889 tests before the last seven cases were added).
Frontend lint, 226 component tests in both configured time zones, and both browser
smokes passed. Two overlapping backend runs hit the unchanged one-second heartbeat
threshold; the four heartbeat tests and both final sequential full runs passed.
Progress reports were regenerated: database 22/22, API 69/69, UI 19/19 and worker
12/12 implemented and tested. The collection run had one stale-source MCP inspection
failure because the module changed after import; the fresh-process check and final
full suites passed. Generated API/database reference pages still await approval of
the required `scripts/docs/` registry and notes edits. No commit or deployment made.

### Specifications, offerings and prices

| Resource | DB column <-> TMF attribute | Filters |
|---|---|---|
| productSpecification | `name`, `description`, `lifecycle_status` <-> `lifecycleStatus`, `updated_at` <-> `lastUpdate` | `name`, `lifecycleStatus` |
| productOffering | `name`, `description`, `lifecycle_status`, `is_sellable` <-> `isSellable`, `valid_for` <-> `validFor`, `product_specification_id` <-> `productSpecification` (ref, optional since 0024), attached prices <-> `productOfferingPrice[]` (refs; inline values create prices), `code` <-> the `offeringCode` entry of `externalIdentifier[]` (0003, 0024; see below), the rest <-> `details` (0024) | `name`, `lifecycleStatus`, `isSellable`, `productSpecification.id`, `externalIdentifier.id` |
| productOfferingPrice | `name`, `lifecycle_status`, `price_type` <-> `priceType` (`recurring`/`oneTime`), `recurring_period` <-> `recurringChargePeriodType` (`monthly`), `recurring_period_length` <-> `recurringChargePeriodLength` (1), `amount` + `currency` <-> `price`, `valid_for` <-> `validFor` | `name`, `priceType`, `lifecycleStatus` |

- **An offering's lifecycle only moves forward**: PATCH `lifecycleStatus` allows In Design
  -> Active and Active -> Retired; Retired is final. Any other transition is a 409 with
  the reason, and nothing in that PATCH is applied. Repeating the current status is not
  a transition (200). POST may still create an offering in any status. The admin UI only
  offers the forward step; the rule lives here. **Specifications and prices follow the
  same rule** (`_check_lifecycle`, `tests/test_api_catalog_lifecycle.py`).
- **An offering's `validFor` is never rewritten in the past**: PATCH may not move
  `startDateTime` to before now, nor set or move `endDateTime` to before now (409);
  repeating the current value is fine. Billing never reads it (only ordering,
  `offeringNotValid`), so ending an offering cannot leave a live product without a price.
- **Prices attach through the offering**, as in the spec: a price is created on its own
  (`product_offering_id` NULL) and attached by `productOfferingPrice[]` on POST/PATCH of
  the offering, or sent inline there and created with it (since 2026-10-18, "Catalog
  attributes" above). A price belongs to at most one offering (409) and may not overlap
  a price of the same type and currency on it (409, the exclusion constraint).
- **Prices are never detached; once their offering was ordered, never re-priced.** A
  PATCH that drops a price from `productOfferingPrice[]` is a 409; `price`, `priceType`
  and the recurring period change only while the offering was never ordered (409
  otherwise, "Catalog attributes" above); `validFor.startDateTime` cannot move (409).
  Changing a price = end its `validFor` and attach a new one - stage 3 billing needs the
  historical price.
- **A price never starts in the past.** A price created or attached with
  `validFor.startDateTime` < now is a 409. Stage 3 catch-up billing charges past days at
  the price valid then, so a past-starting price would back-charge them.
- **Business rule: a new price becomes sellable from the next midnight in BILLING_TZ at
  the earliest.** Its start must be >= now, and an order looks up the price at midnight
  (BILLING_TZ) of the product's start date - so a price created today can only price
  products starting tomorrow or later.
- **A price is never ended retroactively.** `validFor.endDateTime` can only be set or
  moved to a value >= now (the injected clock), and only while the current end, if any,
  is not in the past (409 otherwise): a retroactive end, or reopening a price that has
  already ended, would change prices already used for billed periods.
- **Ending a price never leaves a product without one (D-W18).** Setting or moving the
  end E of a recurring price attached to an offering is a 409 unless a price of the same
  offering, type and currency starts exactly at E, or every product of the offering on an
  account in that currency (not cancelled/aborted) is terminated by the first business
  date whose midnight the price no longer covers (E's date if E is midnight, else the
  next day). A gap could never be filled later: prices never start in the past. A
  missing price is therefore a data bug, fixed case by case with a reviewed one-off script
  and an audit note - there is no waiver feature. Ordering keeps the other side closed
  (`priceEnds`, D-W20): together they give the invariant **"a live product has a price
  for every future day"**.
- **Deviation: changing a live offering's price is one internal operation (D-W21).**
  `POST /internal/v1/productOffering/{id}/priceChange {amount, currency, validFrom}` -
  TMF620 cannot express it: there is no cross-request transaction, and with a product
  live neither order of its two steps works (attaching the successor first violates the
  no-overlap constraint; ending first is refused by the rule above). In one transaction
  it ends the open-ended recurring price in `currency` at midnight (BILLING_TZ) of
  `validFrom` and creates the successor, attached, from that moment; all or nothing
  (proven by a failure injected after the end). `validFrom` is a business date >=
  tomorrow (the next midnight); 409 if there is no open-ended price in that currency or
  a price already starts on or after `validFrom`. Existing products pick up the new price
  from their first billing period starting on or after `validFrom` (the core prices each
  period at its first billable day); new orders from `validFrom`.
- **Offering code (migration 0003; 0024):** the `externalIdentifier[]` entry `{id:
  <code>, externalIdentifierType: "offeringCode", owner: "bss", @type:
  ExternalIdentifier}`, at most one among any other identifiers. `owner` may be omitted
  (any other value is a 422). The code matches `^[A-Z0-9][A-Z0-9_-]{0,31}$` (upper case,
  so uniqueness never depends on case), is unique (409 "offering code X is already in
  use"), optional, and changeable on PATCH since 2026-10-18 ("Catalog attributes"
  above). Offerings without any identifier carry no `externalIdentifier`.
- An offering's `productSpecification` changes on PATCH until the offering is ordered
  (2026-10-18; read-only before).
- **Migration 0002** adds what the API needs and 0001 had no place for. Its NOT NULL
  columns have no default on purpose: if the tables are not empty the migration fails
  and the deploy stops before the app starts.

### Offering kind and one-time prices (issue #187)

An offering's kind is a `productOfferingCharacteristic` named `offeringKind` (`plan`,
`addOn`, `payAsYouGo`; the default is plan), validated on write (400); only a plan has a
recurring price (422). `oneTime` prices, until now stored only, are billed with a prepaid
purchase as fees. See [prepaid.md](prepaid.md#plans-renewal-add-ons-and-fees-2026-11-05-issue-187).

## UI: product specifications (slice 11, 2026-10-25)

Built as designed, frontend only.
The Catalog screen has the tabs Offerings | Specifications (`ui.catalog.specifications`):
name, status, brand, version, the number of loaded offerings using it, last update. A row opens
one dialog (`ui.catalog.editSpecification`) with name, description, brand, version, product
number and a lifecycle select that holds only the current and the next forward status; Save sends
a merge patch of the changed fields (null for an emptied text). Delete
(`ui.catalog.deleteSpecification`, inline confirm) is hidden while a loaded offering uses the
specification, and the server's 409 still shows. `+ New specification`
(`ui.catalog.newSpecification`) posts name, lifecycleStatus (default In Design) and the filled
text fields; New offering has a **New specification…** button that opens the same dialog and
preselects the result. Code: `screens/Specifications.tsx`, `api/bss.ts`. Tests:
`Catalog.test.tsx` (list, create/edit/delete bodies, forward-only lifecycle, delete hidden,
409 messages, preselection) and `tests/test_ui_requests.py::test_specification_writes_as_the_ui_sends_them`.
Deviation: none. The search box filters the Specifications tab by name or brand; the
lifecycle filter shows on the Offerings tab only.

## UI: offering and price detail, edit, delete (slice 12, 2026-10-25)

Built as designed, frontend only.
The offering dialog loads `GET productOffering/{id}` (description, sellable, code, specification,
categories). **Edit offering** (`ui.catalog.editOffering`) sends a merge patch of the changed
fields only; an unchanged `validFor` start is sent as loaded, the code edit rewrites the
`offeringCode` entry and sends every other `externalIdentifier` entry as loaded, categories are a
checkbox list from `GET category` (empty list says so). **Delete** (`ui.catalog.deleteOffering`)
is offered when the loaded offering is In Design and has no attached price; the server's 409
(retire it instead / delete its prices first) is shown. A price line opens the price dialog
(`ui.catalog.priceDetail`): name, description and amount edits (`ui.catalog.editPrice`; the
amount goes out as an exact number token and a 409 adds a pointer to Change price), **End price**
(`ui.catalog.endPrice`, from tomorrow, keeps the loaded start) and **Delete**
(`ui.catalog.deletePrice`). Code: `screens/OfferingEdit.tsx`, `screens/Catalog.tsx`, `api/bss.ts`.
Tests: `Catalog.test.tsx` (detail, bodies, identifiers kept, refusals, delete) and
`tests/test_ui_requests.py::test_offering_and_price_edits_as_the_ui_sends_them`.
Deviations: "has no products" cannot be known in the UI, so Delete hides on status and prices
only and the server's 409 covers the rest; the price dialog is offered Delete always (its
offering's order history is not loaded); only the price amount is a charge field in Edit price
(period fields stay API/MCP).

## UI: categories and product catalogs (slice 13, 2026-10-25)

Built as designed, frontend only.
Catalog tabs **Categories** (`ui.catalog.categories`: tree by `parent`, roots first, status,
offering and subcategory counts) and **Catalogs** (`ui.catalog.catalogs`: name, type, status, root
category names). One dialog per kind (`newCategory`/`editCategory`/`deleteCategory` and the
catalog equivalents) with name, description, parent or catalog type, a forward-only lifecycle,
validity and a checkbox list: the offerings of a category (sent as the whole `productOffering[]`
when the set changed) or the root categories of a catalog (`category[]`). A changed field goes out
alone in a merge patch (null for an emptied text or a category made a root; an unchanged validity
start is sent as loaded). Delete of a category is hidden while it has loaded subcategories or
offerings (the 409 for catalog membership still shows); Delete of a catalog is always offered.
Code: `screens/Classification.tsx`, `api/classification.ts`. Tests: `Catalog.test.tsx`
("categories and catalogs": tree, exact bodies, cycle-free parent select, membership replacement,
delete availability, refusals) and
`tests/test_ui_requests.py::test_category_and_catalog_writes_as_the_ui_sends_them`.
Deviation: the server requires `validFor.startDateTime` whenever `validFor` is sent, so the dialog
refuses a "Valid to" without "Valid from" (the refusal is certain).

## UI: import and export jobs (slice 14, 2026-10-25)

Built as designed, frontend only.
Catalog tab **Import / export** (`ui.catalog.jobs`, `screens/CatalogJobs.tsx`, `api/catalogJobs.ts`):
both job lists merged newest first; export dialog (scope select, then Download and Copy as import),
import dialog (file or pasted JSON, `Failed` keeps the dialog open with the whole `errorLog`), and
a job dialog with Delete ("Nothing it imported is undone."). The document is encoded as UTF-8
base64 into the `data:` URL; the size check is 1,000,000 bytes of the text. Tests:
`Catalog.test.tsx` ("import and export jobs": list, exact bodies including the base64 of fixtures,
Blob and file name, size check, failed log, delete) and
`tests/test_ui_requests.py::test_catalog_jobs_as_the_ui_sends_them` (export, then its import, is a
no-op success, for both scopes). Deviations: "Everything" sends no `query` (an empty one is the
same); the file name date is the job's creation business date, not the browser clock.
