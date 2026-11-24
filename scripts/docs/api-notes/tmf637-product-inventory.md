## Behaviour and deviations

- **Subscriber numbers (N4):** products whose creation specification requires built-in
  `msisdn` derive `realizingResource` from their assignment at the business date (future
  products: their start date). Change numbers through TMF622 modify orders; direct PATCH
  of their resource list gives 409. `realizingResource.id` filters these effective
  assignments before counts/paging. Products without the requirement retain unchecked
  resource documents. Number-change items appear with role `modification`.

- **Review update (2026-10-19):** a future product accepts an exclusive
  `terminationDate` after its start, including on POST. It remains `created` until
  start, then `pendingTerminate`, then `terminated`; catching up past both dates
  moves directly to `terminated`. A delete on or before a future start cancels it
  with zero billable days. See `tests/orders/test_api_product_writes.py::test_future_delete_dates`.
- A same-day suspension/resumption can be undone through TMF622 `cancelProductOrder`;
  its previous status is restored and a ProductStateChangeEvent emitted. An older
  change remains a 409. See `tests/orders/test_api_suspension.py::test_same_day_status_change_can_be_undone`.

- **Writes are orders.** Products are only created, changed and ended by orders (TMF622):
  POST `/product` places an add order (with `terminationDate`, a delete order after it,
  in the same transaction), PATCH `terminationDate` a delete order with that date, PATCH
  `productOffering` a modify order changing the offering from today, PATCH `status`
  (`suspended` / `active`) a modify order suspending or resuming it from today - one
  order per PATCH (422 otherwise) - and DELETE a delete order effective today (the
  product stays readable, ended). An order that would be rejected is a 409 with its
  reasons, nothing stored. `billingAccount` and `startDate` may only repeat their value
  (409 otherwise); the other derived attributes are read-only (`app/api/inventory.py`;
  `tests/orders/test_api_product_writes.py`, `tests/tmf/test_api_product_attributes.py`,
  `tests/orders/test_api_suspension.py::test_patch_status_places_the_order`).
- **Descriptive attributes** (`description`, `isBundle`, `isCustomerVisible`,
  `productSerialNumber`, `agreementItem`, `intent`, `place`, `product`,
  `productCharacteristic`, `productRelationship`, `productTerm`, `realizingResource`,
  `realizingService`) are stored as sent, checked against the spec, and change directly
  on PATCH (null removes one) - they neither create nor end a product
  (`tests/tmf/test_api_product_attributes.py`).
- `productOffering.version`, when sent, must be the offering's current version (422).
- `productOrderItem[]` holds the order items that created (`role: creation`), changed
  (`role: modification`: offering or status) and terminated (`role: termination`) the
  product, as `RelatedOrderItem` (`::test_product_view`,
  `::test_terminated_product_points_at_both_order_items`).
- `terminationDate` is **exclusive**: the first day no longer billed. `startDate` and
  `terminationDate` are dates in BILLING_TZ, sent as midnight of that day.
- `name` is the product's own: its offering's name at creation unless POST or PATCH set
  one; an offering change does not rename it. `creationDate` and `orderDate` come from
  the creating order's injected clock. `productSpecification` and the owning Customer's
  `relatedParty` reference are returned and can be followed.
- `productPrice[]` shows the full charge-interval fee selected at the charge period's start
  (or activation within the first period), so a mid-period catalog change waits until
  the next billing period. Future products use their start date, ended products their
  last billable period, and cancelled/aborted products have no price. The price is not
  the prorated first charge. `tests/tmf/test_api_strict_inputs_and_quotes.py` proves the boundaries.
  `priceAlteration` is always empty (billing applies no alteration); `unitOfMeasure` is
  the catalog price's, as text.
- `status` follows dates through the billing worker: a product ordered for a future date
  stays `created` until the run of its start date flips it to `active`; `pendingTerminate`
  becomes `terminated` on its termination date; a suspension or resumption ordered for a
  later day takes effect with that day's run (no pending state: the spec has none)
  ([billing-worker.md](../billing-worker.md#status-flips),
  [suspension](../decisions/tmf-api.md#suspension-2026-10-19)). Billing never depends on
  it: a `suspended` product is billed by its dates. `pendingActive` and `aborted` are
  never reached (orders complete synchronously).
