# Catalog: TMF620 mapping and price rules

Decision records moved from CLAUDE.md (as of `910ec0a`), unchanged in meaning. The rules
needed every session stay in [CLAUDE.md](../../CLAUDE.md); `scripts/check-decisions.py`
proves nothing was lost in the move. Index: [README.md](README.md). Shared API
conventions: [tmf-api.md](tmf-api.md).

## TMF620 mapping

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
| productOffering | `name`, `description`, `lifecycle_status`, `is_sellable` <-> `isSellable`, `valid_for` <-> `validFor`, `product_specification_id` <-> `productSpecification` (ref, mandatory - ours), attached prices <-> `productOfferingPrice[]` (refs), `code` <-> `externalIdentifier[]` (0003; see below) | `name`, `lifecycleStatus`, `isSellable`, `productSpecification.id`, `externalIdentifier.id` |
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
  the offering; only references are accepted there, not inline prices. A price belongs to
  at most one offering (409) and may not overlap a price of the same type and currency on
  it (409, the exclusion constraint).
- **Prices are never detached or re-priced.** A PATCH that drops a price from
  `productOfferingPrice[]` is a 409; `price`, `priceType` and the recurring period are
  read-only (400); `validFor.startDateTime` cannot move (409). Changing a price = end its
  `validFor` and attach a new one - stage 3 billing needs the historical price.
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
- **Offering code (migration 0003):** the spec's `externalIdentifier[]` with exactly one
  entry `{id: <code>, externalIdentifierType: "offeringCode", owner: "bss", @type:
  ExternalIdentifier}`. `owner` may be omitted on POST (any other value is a 422). The
  code matches `^[A-Z0-9][A-Z0-9_-]{0,31}$` (upper case, so uniqueness never depends on
  case), is unique (409 "offering code X is already in use"), optional, set on POST only
  and read-only after (400). Offerings without one carry no `externalIdentifier`.
- **Deviation: only references in `productOfferingPrice[]`.** The spec's
  `ProductOfferingPriceRefOrValue` also allows an inline price; we accept references only
  (an entry without `id` is a 422). Create the price first, then reference it.
- `productSpecification` of an offering is read-only on PATCH.
- **Migration 0002** adds what the API needs and 0001 had no place for. Its NOT NULL
  columns have no default on purpose: if the tables are not empty the migration fails
  and the deploy stops before the app starts.
