# Catalog: TMF620 mapping and price rules

Decision records moved from CLAUDE.md (as of `910ec0a`), unchanged in meaning. The rules
needed every session stay in [CLAUDE.md](../../CLAUDE.md); `scripts/check-decisions.py`
proves nothing was lost in the move. Index: [README.md](README.md). Shared API
conventions: [tmf-api.md](tmf-api.md).

## TMF620 mapping

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
