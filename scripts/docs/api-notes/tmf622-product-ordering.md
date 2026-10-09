## Behaviour and deviations

Orders are the only way to create or terminate products. POST processes the order
**synchronously, in the request's transaction** (`app/ordering/process.py`,
`place_order`). There is no PATCH, DELETE or cancelProductOrder (405 / 404:
`tests/test_api_ordering.py::test_orders_cannot_be_changed`,
`::test_cancel_product_order_is_not_exposed`).

**4xx vs rejected.** A request that cannot even be stored is a 4xx and stores nothing:
malformed JSON, an unknown attribute or an empty `productOrderItem[]` (400); a missing
mandatory field, a reference to a billing account, offering or product that does not
exist, duplicate item ids, a product in two delete items, `quantity` other than 1, an add
item without `productOffering` (or with a `product`), a delete item without `product` (or
with a `productOffering`) (422) - `app/api/ordering.py` (`_item_request`),
`place_order` (`OrderRefused`); `::test_malformed_orders_are_4xx_and_not_stored`,
`::test_malformed_delete_items`. Everything else that is wrong is a **business
rejection: the order is stored with state `rejected` and its reasons in
`productOrderErrorMessage[]`, and the answer is 201**.

| Rejection code (ours) | Level | When | Proven by |
|---|---|---|---|
| `accountNotActive` | order | the billing account is not `Active` | `::test_account_must_be_active_and_start_not_in_the_past` |
| `requestedStartDateInPast` | order | requested date before today (BILLING_TZ) | same |
| `actionNotSupported` | item | `modify` or `noChange` | `::test_modify_and_no_change_are_stored_as_rejected` |
| `billingAccountMismatch` | item | the item's `billingAccount` differs from the order's | `tests/test_api_ordering.py::test_delete_rules` (a delete item; `_item_problems` checks it before the action) |
| `offeringNotSellable` | item | offering not `isSellable` or not `Active` | `::test_offering_must_be_sellable_and_priced` |
| `offeringNotValid` | item | offering `validFor` does not cover midnight of the start date | `::test_offering_and_price_must_be_valid_at_the_start_date` |
| `noRecurringPrice` | item | no recurring price in the account currency at that midnight | `::test_offering_must_be_sellable_and_priced` |
| `priceEnds` | item | from the start date, the recurring prices do not form a gapless chain ending open-ended (D-W20) | `tests/test_order_price_chain.py` |
| `productOfOtherAccount`, `productAlreadyEnded`, `openDeleteItem`, `productNotStarted` | item | delete rules | `tests/test_api_ordering.py::test_delete_rules`, `::test_a_product_with_an_open_delete_elsewhere_is_rejected` |

- **All or nothing:** one reason anywhere rejects the whole order; failing items are
  `rejected`, the others `cancelled`, nothing is executed (`tests/test_api_ordering.py::test_one_bad_item_rejects_the_whole_order`).
  A rejected order's items are stored directly in their final states, so a rejected
  delete item never collides with the one-open-delete index.
- **States** come from the pure state machine `app/ordering/states.py`: order
  `acknowledged -> inProgress -> completed` or `acknowledged -> rejected`; items the same
  plus `acknowledged -> cancelled` (`tests/test_order_states.py`).
- **`requestedStartDate`** (our interpretation; TMF has no per-item date) is the start
  date of add items and the termination date of delete items, converted to the date in
  BILLING_TZ; it defaults to today and may not be in the past.
- **add** creates the product: `active` if it starts today, `created` if later (the
  worker flips it on its start date) - `tests/test_api_ordering.py::test_add_creates_an_active_product`.
- **delete** of a started product sets the exclusive `terminationDate`: `terminated`
  for today, `pendingTerminate` for a future date (`::test_delete_terminates_a_started_product`,
  `::test_delete_with_a_future_date_is_pending_until_then`). **delete of a product not
  started yet cancels it** (`cancelled`, `terminationDate = startDate`, 0 billable days);
  a requested date after its start date is `productNotStarted`
  (`::test_delete_of_a_future_product_cancels_it_with_zero_billable_days`).
- **Row lock:** the products of delete items are locked `FOR UPDATE` in id order before
  anything about them is read; a concurrent second delete waits, then is rejected
  `productAlreadyEnded` (`tests/test_order_concurrency.py`).
- **Deviation:** `billingAccount` is mandatory on the order (ours).
