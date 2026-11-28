# Product offering qualification: one eligibility check before an order (2026-11-28, issue #429)

Before this, eligibility was checked in several places: the device check
(`GET /internal/v1/device/check`, [network.md](network.md#device-issue-194)), service-address and
E911 rules inside the order, prepaid funds and the multi-line tier only in the firstCharge
quote, and number availability in the number picker. A bad combination showed up only when
the TMF622 order came back `rejected`. TMF679 is the TMF answer to that: one question, "can
this account get this offering, with this device, number and address?", answered before the
order is placed. Design decided under the standing delegation (the plan in the issue,
approved by the human 2026-11-28). This slice is the backend and the MCP tools. The UI (the
Add product dialog shows the answer before Submit) is #527.

## The API (vendored, `spec/`)

**TMF679 Product Offering Qualification v5.0.0**, base
`/tmf-api/productOfferingQualification/v5` (its `servers` URL). Both resources,
`checkProductOfferingQualification` and `queryProductOfferingQualification`: `GET` list and by
id, `POST`, `PATCH`, `DELETE`, and `/hub` with `GET /hub/{id}`, as the spec defines them.
API notes: [docs/api/tmf679-product-offering-qualification.md](../api/tmf679-product-offering-qualification.md).

## Which checks: all of the order's, by running the order

**Decision: a check is a dry run of the order engine, not a second set of validators.** The
check items become one TMF622 `OrderRequest` (`app/api/qualification.py`), which
`app/ordering/qualification.py` places with `place_order` inside a savepoint. It reads back
the order's `productOrderErrorMessage` rows and rolls the savepoint back. Each error becomes an
`eligibilityResultReason {code, label}` with the order's code and reason.

- **Why:** the issue asks that every reason that rejects an order has a matching qualification
  reason. A separate list of rules would drift from the order the first time a rule changes.
  Running the engine makes the two identical by construction, including rules that
  live deep in it: devices, E911, number and SIM availability, the SIM swap lock, the add-on
  pack's plan, prepaid funds with tax and the multi-line tier, `numberRequirementDiffers`,
  offering validity and price gaps. A new order rule is a qualification reason the day it
  lands.
- **Nothing stays.** Everything the engine writes is in the request's transaction and is
  undone with the savepoint: the order, products, number claims, prepaid holds, network
  commands, the order's and products' events. Nothing in the order path commits on its own
  or calls out beyond the network's read-only device check (verified when designing:
  no NOTIFY, no second connection). `tests/orders/test_qualification.py` counts those
  tables before and after a check, and checks a prepaid balance is untouched.
- **Locks.** The engine's row locks (the account, the named products, the numbers) are held
  until the request's transaction ends, as an order's are. A check waits for a concurrent
  order on the same account and sees its outcome, and an order waits for a running check. A
  check is one short transaction, so that is acceptable.
- **No PIN.** The request carries no actor, so the agent's PIN screening is skipped. A PIN
  authenticates an order and says nothing about eligibility, and a dry run must not count
  PIN attempts. `ORDER_ONLY` in `app/ordering/qualification.py` lists the order refusals a
  qualification never gives, with their reasons: the four PIN codes, and `portOutLocked`,
  which only a carrier's port-out meets. The SIM swap lock still applies.
  `tests/orders/test_qualification.py::test_every_order_refusal_is_a_qualification_reason`
  lists every order refusal code (`frontend/src/api/orderErrors.codes.json`, itself locked to
  the engine by `test_order_error_codes.py`) against that table.
- **Mapping an item.** A check item takes exactly what an order item takes: `action` (default
  `add`), `productOffering`, `product` (`id`, `status`, `productCharacteristic` imei/simType,
  `place`, `realizingResource`, `productRelationship`) and `qualificationItemRelationship`
  (as `productOrderItemRelationship`). The TMF622 item parser does the checking
  (`app/api/ordering.py::item_request`, now public), so a malformed item gets the same 4xx a
  malformed order item would. TMF679 has no account field, so the billing account goes in
  `product.billingAccount`, or comes from the product a modify, delete or noChange item names
  (the order's own derivation). `expectedActivationDate` is the order's `requestedStartDate`:
  the items are one order, which starts on one day, so differing dates are a 422.
- **The answer.** An item is `qualified` when no reason names it or the order as a whole
  (an order-level reason such as `insufficientFunds` or `requestedStartDateInPast`
  disqualifies every item). `qualificationResult` is `unqualified` when any reason exists.
  All-or-nothing, as for orders: a qualified item next to an unqualified one would still be
  cancelled in that order.
- **Flags.** `provideResultReason: false` leaves the reasons out. `provideOnlyAvailable: true`
  answers only the qualified items. `provideAlternative` is kept as sent: we propose no
  alternates, because the catalog has no rule for what replaces what.

## The query

`queryProductOfferingQualification` lists the offerings a billing account
(`searchCriteria.product.billingAccount`) can get. It qualifies each Active, sellable
offering as an add item, together with the rest of `searchCriteria.product` (a device, an
add-on pack's plan), or only `searchCriteria.productOffering` when one is named. An offering
is listed when nothing stands against it except a missing input that a query cannot supply:
`numberRequired`, `imsiRequired`, `serviceAddressRequired`, `e911AddressRequired`,
`addOnParentRequired` (`NEEDS_INPUT`).

- **Known limit, recorded:** the engine checks prepaid funds only when nothing else stands
  against the order. A plan that needs a number is therefore listed whatever the balance; the
  check with the number gives the full answer (tested). Offerings that need no input, such as
  add-on packs and plans without a number, are checked for funds by the query too.
- **Cost:** one dry run per offering. The catalog has dozens, so a query qualifies at most 100
  (`MAX_CANDIDATES`). A bigger catalog gets a 422 asking it to name one offering, instead of
  a slow request.

## Sync only, storage and retention

- **Sync only.** Every qualification is answered in the POST: `state` is `done` and
  `instantSyncQualification` true (a false is a 422, "set by the server"). `StateChange`
  events are never sent. Nothing in the engine is slow enough to need the asynchronous
  form.
- **Stored as answered** in `product_offering_qualification` (migration 0092), one table for
  both kinds. The answer items are jsonb as answered; `details` holds what the client sent
  that has no behaviour (description, channel, note, relatedParty,
  requestedQualificationCompletionDate, the flags, a query's searchCriteria). PATCH changes
  only what describes it. The question and the answer are read-only. DELETE removes it.
- **No foreign key on the billing account:** a qualification is short-lived, so it never
  blocks deleting an account created by mistake ([tmf-api.md](tmf-api.md#deleting-parties-customers-and-accounts-2026-10-17)).
- **Expiry and retention.** `expirationDate` is the end of the qualification's day
  (BILLING_TZ), because prices, balances and free numbers move after it. A qualification is
  an answer, not a reservation. The worker's housekeeping deletes a row 30 days after
  creation (`app/repository/housekeeping.py`, [data-model.md](data-model.md#retention-2026-11-11-282)).

## The device check stays

`GET /internal/v1/device/check?imei=` (and MCP `check_device`) is **kept as it is**. It
answers what the network knows about a device (its compatibility result and reasons) without
an account or an offering. The subscriber's device screen uses it (its Check button). A qualification answers a
different question (may this account order this, with this device) and includes the same
network check through the engine. Folding one into the other would make each worse.

## Deviations (`tests/tmf-gaps.txt`)

Read-only on PATCH: the answer and the question. Not taken on an item: category, promotion,
note, terminationError, nested items. Never returned: alternates. A query item carries only
its offering. A check needs its items and a query its searchCriteria.
