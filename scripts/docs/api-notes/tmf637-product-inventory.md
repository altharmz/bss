## Behaviour and deviations

- **Read-only.** Products are only written by orders (TMF622); POST/PATCH/DELETE on
  `/product` are 405 (`app/api/inventory.py`;
  `tests/test_api_inventory_bill.py::test_products_are_not_written_through_tmf637`).
- `productOrderItem[]` holds the order items that created (`role: creation`) and
  terminated (`role: termination`) the product, as `RelatedOrderItem`
  (`::test_product_view`, `::test_terminated_product_points_at_both_order_items`).
- `terminationDate` is **exclusive**: the first day no longer billed. `startDate` and
  `terminationDate` are dates in BILLING_TZ, sent as midnight of that day.
- `name` is the offering's name; `creationDate` and `orderDate` come from the creating
  order's injected clock. `productSpecification` and the owning Customer's
  `relatedParty` reference are returned and can be followed.
- `productPrice[]` shows the full monthly fee selected at the billing period's start
  (or activation within the first period), so a mid-period catalog change waits until
  the next billing period. Future products use their start date, ended products their
  last billable period, and cancelled/aborted products have no price. The price is not
  the prorated first charge. `tests/test_api_read_completeness.py` proves the boundaries.
- `status` follows dates through the billing worker: a product ordered for a future date
  stays `created` until the run of its start date flips it to `active`; `pendingTerminate`
  becomes `terminated` on its termination date ([billing-worker.md](../billing-worker.md#status-flips)).
  Billing never depends on it.
