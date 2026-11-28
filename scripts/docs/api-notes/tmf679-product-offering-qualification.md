## Behaviour and deviations

- Both tasks are answered synchronously: POST answers 201 with `state` `done` and
  `instantSyncQualification` true (sending false is a 422). The answer is stored and readable
  by id; the worker's housekeeping deletes it 30 days after creation. `expirationDate` is the
  end of the qualification's day (BILLING_TZ). Design: docs/decisions/qualification.md.
- **A check is a dry run of the order engine.** The items are placed as one TMF622 order
  inside a savepoint that is then rolled back, so nothing is written or reserved (no order,
  product, number claim, prepaid hold, network command or event). Each
  `eligibilityResultReason` is a `productOrderErrorMessage` of that order: `code` is the
  order's code, `label` its reason. An item is `unqualified` when a reason names it or the
  order as a whole, and `qualificationResult` is `unqualified` when any item is. The agent's
  PIN is not checked (no PIN codes); a SIM swap lock is.
- A check item takes what a TMF622 order item takes: `action` (default `add`),
  `productOffering`, `product` (`id`, `status`, `productCharacteristic` imei/simType,
  `place`, `realizingResource`, `productRelationship`) and `qualificationItemRelationship`.
  The billing account is `product.billingAccount`, or the account of the product a modify,
  delete or noChange item names. `expectedActivationDate` is the order's
  requestedStartDate: one date for all items (422 otherwise), default today. A malformed item
  is the 4xx a malformed order item is.
- `provideResultReason: false` leaves the reasons out; `provideOnlyAvailable: true` answers
  only the qualified items (the result still covers all). No alternates are proposed:
  `provideAlternative` is kept as sent.
- **A query** qualifies each Active, sellable offering (at most 100; a bigger catalog names
  one in `searchCriteria.productOffering`) as an add item for the account in
  `searchCriteria.product.billingAccount`, with the rest of `searchCriteria.product` (a
  device, an add-on pack's plan). An offering is listed when nothing but a missing input
  stands against it: `numberRequired`, `imsiRequired`, `serviceAddressRequired`,
  `e911AddressRequired`, `addOnParentRequired`. The engine checks prepaid funds only once
  nothing else stands against an order, so a plan that needs a number is listed whatever
  the balance: check it with the number before ordering.
- PATCH changes what describes a qualification: `description`, `channel`, `note`,
  `relatedParty`, `requestedQualificationCompletionDate`. The question and the answer are
  read-only (400). DELETE removes it.
- Events: Create, AttributeValueChange (PATCH) and Delete of both; StateChange is never
  sent (a qualification is created `done`).
- Lists filter by `id`, `state`, `billingAccount.id` and, for checks,
  `qualificationResult`, newest first.
