## Behaviour and deviations

- **Read-only.** Products are only written by orders (TMF622); POST/PATCH/DELETE on
  `/product` are 405 (`app/api/inventory.py`;
  `tests/test_api_inventory_bill.py::test_products_are_not_written_through_tmf637`).
- `productOrderItem[]` holds the order items that created (`role: creation`) and
  terminated (`role: termination`) the product, as `RelatedOrderItem`
  (`::test_product_view`, `::test_terminated_product_points_at_both_order_items`).
- `terminationDate` is **exclusive**: the first day no longer billed. `startDate` and
  `terminationDate` are dates in BILLING_TZ, sent as midnight of that day.
- `name` is the offering's name; `creationDate` is the row's `created_at` (the
  database's clock, not the injected one).
- `status` follows dates through the billing worker: a product ordered for a future date
  stays `created` until the run of its start date flips it to `active`; `pendingTerminate`
  becomes `terminated` on its termination date ([billing-worker.md](../billing-worker.md#status-flips)).
  Billing never depends on it.
