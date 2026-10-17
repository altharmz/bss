# TMF conformance gap analysis

**Following TMF (2026-10-17).** Rule: what the vendored specs define is implemented, the
rest skipped ([the decision](decisions/tmf-api.md#following-tmf-2026-10-17)). The
attribute-level gaps are no longer tracked in this file: **`tests/tmf-gaps.txt` is the
exact, test-locked list** (831 lines on 2026-10-17), produced by the probe in
`tests/tmf_gaps.py`. Sections 3 and 5 below are superseded by it. What it cannot probe,
and is still open:

- `paymentMethod.@type` of a payment rewritten to the method's name (C5; refunds keep
  both).
- Order items returned in text order of their ids, not as sent (C7).
- Item `billingAccount` checked, never stored or returned (C8).
- A client-supplied `id` on POST (`Addressable_FVO` has one) is a 400.
- States never reached (section 4): order item actions `modify`/`noChange`; order
  `held`, `pending`, `failed`, `partial`, `draft`, `inProgress.accepted`,
  `assessingCancellation`, `pendingCancellation`; product `pendingActive`, `suspended`,
  `aborted` (and C9: a product on a non-Active account cannot be ended by an order);
  payment states other than `done`; party `status`.
- priceType `discount`, `allowance`, `penalty` (TMF620's description of `priceType`).
- Nested response attributes (the probe asks for top-level ones only).

**Complete (2026-10-17).** The last seven operations - TMF637 product POST/PATCH/DELETE
(as orders placed on the caller's behalf), TMF666 billingCycleSpecification
POST/PATCH/DELETE (named instances of the one monthly policy) and TMF622 PATCH
productOrder (descriptive attributes) - bring coverage to **129/129** of the vendored
specs' non-listener operations. What remains is the subscriber's side (`/listener/*`)
and the deviations recorded in [tmf-api.md](decisions/tmf-api.md).

**Event notifications (2026-10-17).** `/hub` in all eight APIs (17 operations), with
events stored transactionally and delivered by the worker with retries. Coverage:
122/129 (94.6%). The seven left are excluded by decision: TMF637 product POST/PATCH/DELETE
(orders alone create or end products), billingCycleSpecification POST/PATCH/DELETE (one
fixed policy) and PATCH productOrder. `/listener/*` is the subscriber's side. See
[the decision](decisions/events.md).

**Order cancellation (2026-10-17).** cancelProductOrder (3 operations) undoes a completed
order before any of it takes effect; DELETE productOrder removes rejected orders.
Non-listener operation coverage: 105/129 (81.4%). TMF622 lacks PATCH productOrder and
`/hub`. See [the decision](decisions/tmf-api.md#order-cancellation-and-deleting-rejected-orders-2026-10-17).

**Party, customer and account deletes (2026-10-17).** DELETE on individual,
organization, customer, billingAccount, financialAccount, partyAccount,
settlementAccount, billFormat and billPresentationMedia, for entries nothing references
(a billing account: no history at all), brings non-listener operation coverage to
101/129 (78.3%). TMF632 and TMF629 now lack only `/hub`; TMF666 lacks
billingCycleSpecification writes and `/hub`. See
[the decision](decisions/tmf-api.md#deleting-parties-customers-and-accounts-2026-10-17).

**TMF620 import and export jobs (2026-10-16).** importJob and exportJob (eight
operations) move the catalog as an inline data: URL document, synchronously, imports
all or nothing under POST's rules. Non-listener operation coverage: 92/129 (71.3%);
TMF620 lacks only `/hub`. See
[the catalog decision](decisions/catalog.md#import-and-export-jobs-2026-10-16).

**TMF620 catalog deletes (2026-10-16).** DELETE on productSpecification,
productOffering, productOfferingPrice, category and productCatalog, for entries nothing
references (D1 narrowed to TMF620), brings non-listener operation coverage to 84/129
(65.1%). TMF620 now lacks only importJob/exportJob and `/hub`. See
[the catalog decision](decisions/catalog.md#deleting-catalog-entries-2026-10-16).

**TMF666 accounts (2026-10-16).** financialAccount, partyAccount and settlementAccount
list/retrieve/create/PATCH (12 operations), with the `financialAccount` link on billing,
party and settlement accounts, bring non-listener operation coverage to 79/129 (61.2%).
TMF666 now lacks only DELETE, `/hub` and the two `billingCycleSpecification` writes. See
[the accounts decision](decisions/accounts.md).

**Refund/bill-delivery milestone (2026-10-16).** Refunds (3 operations), on-demand
bills (3), formats and presentation media (8) bring non-listener TMF operation
coverage to 67/129 (51.9%). Account preferences drive immutable JSON bill documents;
refunds consume real unused credit. See [the decision](decisions/billing-completion.md)
for supported behavior and limits. Older tables below remain a dated snapshot.

**Catalog organization follow-up (2026-10-16).** Category and product catalog
list/retrieve/create/PATCH add eight operations, bringing non-listener operation
coverage to 53/129 (41.1%). Category hierarchies, catalog root membership and
category/offering navigation are supported. See [the catalog decision](decisions/catalog.md#categories-and-product-catalogs-2026-10-16).
The original analysis below remains a dated snapshot.

Analysis only - nothing in the code was changed. Written 2026-10-12 against `master` at
`94200ee`.

**Implementation follow-up (2026-10-14).** The input/read-completeness slice addresses
C1's POST `@type` validation, C2's unsupported comma filters, C6's write queries, and
B1/B2/B3's prices, navigation and bill-cycle reads. The tables below remain the dated
analysis of `94200ee`; current behavior and the limits on historical quotes are in
[decisions/tmf-api.md](decisions/tmf-api.md#input-correctness-and-read-completeness-2026-10-14).
The accepted `@baseType`/`@schemaLocation` metadata is still not persisted.

**Billing policy follow-up (2026-10-14).** TMF666's billing cycle specification
list/retrieve operations now expose the supported monthly policy, referenced by
billing accounts. Together with the two bill-cycle reads above, this brings the
implemented non-listener operations to 45/129 (34.9%). The dated tables below are
unchanged; see [the current decision](decisions/tmf-api.md#billing-cycle-specification-discovery-2026-10-14)
for the read-only policy and its distinction from an account's calendar anchor.

**Sources.** The vendored specs in `spec/` (versions and upstream commits in
[spec/README.md](../spec/README.md)), the code in `app/` and `bss_mcp/`, the tests, and the
decisions in [CLAUDE.md](../CLAUDE.md) and [decisions/](decisions/README.md). The "not
exposed" list that used to be in CLAUDE.md is now in
[decisions/tmf-api.md](decisions/tmf-api.md) (line 17); CLAUDE.md says the files in
`docs/decisions/` "bind just as much". Spec facts were read from the YAML/JSON with
`yaml.safe_load` (`$ref`/`allOf` resolved), not from memory.

**Not consulted.** No TM Forum material outside `spec/`: not the TMF630 REST design
guidelines, not conformance profiles, not the TM Forum GitHub beyond what `spec/README.md`
already pins. Where section 5 talks about TMF630 behaviour that the vendored specs do not
encode themselves (filter operators, `sort`, comma-separated values), it says so and
leaves the claim unverified. API numbers in section 7 that are not named in `spec/` are
marked as such.

**Classification used.** *Excluded by decision* - a recorded decision says so (quoted).
*Not implemented* - nothing decides it either way. *Deviation by decision* - implemented
differently from the spec on purpose.

The decisions quoted most often:

- **D1** - [decisions/tmf-api.md](decisions/tmf-api.md), "TMF API conventions": "**Not
  exposed:** DELETE anywhere, POST/PATCH on product, cancelProductOrder, `/hub`, the
  billing run. An unsupported method on an exposed path is a 405."
- **D2** - same file, "TMF622 mapping": "**GET list, GET by id, POST only.** PATCH/DELETE
  are 405, cancelProductOrder is 404."
- **D3** - CLAUDE.md: "Orders alone create or end products; only the billing run creates
  ABRs and bills."
- **D4** - [decisions/data-model.md](decisions/data-model.md): "**Out of scope for now:**
  taxes, `modify` items and cancelProductOrder."
- **D5** - [overview.md](overview.md), "Scope": "Out of scope: ... event notifications
  (`/hub`, listeners), payment processing (a payment is recorded as received)."
- **D6** - decisions/tmf-api.md: "**PATCH** accepts `application/merge-patch+json` only
  (415 otherwise)".

---

## 1. Operations

Every path + method of the vendored specs (`paths`), except `/listener/*`, which section 6
covers. Implemented = a route in `app/api/<module>.py`. Total: 129 spec operations, 41
implemented (TMF620 12/35, TMF622 3/10, TMF629 4/8, TMF632 8/12, TMF637 2/7, TMF666
4/37, TMF676 3/8, TMF678 5/12).

### TMF620 Product Catalog v5.0.0 (`app/api/catalog.py`)

| Operation | Status |
|---|---|
| GET/POST `/productSpecification`, GET/PATCH `/productSpecification/{id}` | implemented (`catalog.py:136-176`) |
| GET/POST `/productOffering`, GET/PATCH `/productOffering/{id}` | implemented (`catalog.py:505-566`) |
| GET/POST `/productOfferingPrice`, GET/PATCH `/productOfferingPrice/{id}` | implemented (`catalog.py:243-331`) |
| DELETE `/productSpecification/{id}`, `/productOffering/{id}`, `/productOfferingPrice/{id}` | excluded by decision (D1, "DELETE anywhere") |
| GET/POST `/category`, GET/PATCH `/category/{id}` | not implemented |
| GET/POST `/productCatalog`, GET/PATCH `/productCatalog/{id}` | not implemented |
| GET/POST `/importJob`, GET `/importJob/{id}`; same for `/exportJob` | not implemented |
| DELETE `/category/{id}`, `/productCatalog/{id}`, `/importJob/{id}`, `/exportJob/{id}` | excluded by decision (D1) |
| POST `/hub`, DELETE `/hub/{id}` | excluded by decision (D1, "`/hub`") |

### TMF622 Product Ordering v5.0.0 (`app/api/ordering.py`)

| Operation | Status |
|---|---|
| GET/POST `/productOrder`, GET `/productOrder/{id}` | implemented (`ordering.py:160-189`) |
| PATCH `/productOrder/{id}` | excluded by decision (D2) |
| DELETE `/productOrder/{id}` | excluded by decision (D1, D2) |
| GET/POST `/cancelProductOrder`, GET `/cancelProductOrder/{id}` | excluded by decision (D1, D2, D4) |
| POST `/hub`, DELETE `/hub/{id}` | excluded by decision (D1) |

### TMF629 Customer v5.0.1 (`app/api/customer.py`)

| Operation | Status |
|---|---|
| GET/POST `/customer`, GET/PATCH `/customer/{id}` | implemented (`customer.py:73-118`) |
| DELETE `/customer/{id}` | excluded by decision (D1) |
| POST `/hub`, GET `/hub/{id}`, DELETE `/hub/{id}` | excluded by decision (D1). TMF629 is the only vendored spec with a GET on `/hub/{id}`. |

### TMF632 Party v5.0.0 (`app/api/party.py`)

| Operation | Status |
|---|---|
| GET/POST `/individual`, GET/PATCH `/individual/{id}` | implemented (`party.py:90-117`) |
| GET/POST `/organization`, GET/PATCH `/organization/{id}` | implemented (`party.py:139-161`) |
| DELETE `/individual/{id}`, `/organization/{id}` | excluded by decision (D1) |
| POST `/hub`, DELETE `/hub/{id}` | excluded by decision (D1) |

### TMF637 Product Inventory v5.0.0 (`app/api/inventory.py`)

| Operation | Status |
|---|---|
| GET `/product`, GET `/product/{id}` | implemented (`inventory.py:96-114`) |
| POST `/product`, PATCH `/product/{id}` | excluded by decision (D1, "POST/PATCH on product"; D3) |
| DELETE `/product/{id}` | excluded by decision (D1) |
| POST `/hub`, DELETE `/hub/{id}` | excluded by decision (D1) |

### TMF666 Account v5.0.0 (`app/api/account.py`)

| Operation | Status |
|---|---|
| GET/POST `/billingAccount`, GET/PATCH `/billingAccount/{id}` | implemented (`account.py:196-253`) |
| GET/POST/GET-id/PATCH on `/billFormat`, `/billPresentationMedia`, `/billingCycleSpecification`, `/financialAccount`, `/partyAccount`, `/settlementAccount` (24 operations) | not implemented |
| DELETE on all seven resources | excluded by decision (D1) |
| POST `/hub`, DELETE `/hub/{id}` | excluded by decision (D1) |

### TMF676 Payment v4.0.0 (`app/api/payment.py`)

| Operation | Status |
|---|---|
| GET/POST `/payment`, GET `/payment/{id}` | implemented (`payment.py:175-204`). The v4 spec itself has no PATCH or DELETE on `/payment`. |
| GET/POST `/refund`, GET `/refund/{id}` | not implemented |
| POST `/hub`, DELETE `/hub/{id}` | excluded by decision (D1) |

### TMF678 Customer Bill v5.0.0 (`app/api/bill.py`)

| Operation | Status |
|---|---|
| GET `/customerBill`, GET/PATCH `/customerBill/{id}` | implemented (`bill.py:171-200`; PATCH = `state` only) |
| GET `/appliedCustomerBillingRate`, GET `/appliedCustomerBillingRate/{id}` | implemented (`bill.py:245-265`) |
| GET `/billCycle`, GET `/billCycle/{id}` | not implemented |
| GET/POST `/customerBillOnDemand`, GET `/customerBillOnDemand/{id}` | not implemented. An implementation is bound by D3: the bill would have to come from the billing run, not from the request handler. |
| POST `/hub`, DELETE `/hub/{id}` | excluded by decision (D1) |

**Base paths.** The specs do not agree with themselves. TMF629's `servers` says
`{apiRoot}/tmf-api/customer/v5/`, but 11 of its example hrefs say
`tmf-api/customerManagement/v5`; TMF632's examples say `tmf-api/party/v5` (60 times) while
its `servers` says `/partyManagement/v5/`; TMF676 v4's `basePath` is `/payment/v4/`. Ours
(decisions/tmf-api.md, "Paths") use `customerManagement`, `partyManagement` and
`paymentManagement/v4`. A client generated from `servers`/`basePath` needs its base URL
overridden for TMF629 and TMF676. Deviation by decision; low priority.

## 2. Resources not exposed

| API | Resource (spec schema) | Status | What we have instead |
|---|---|---|---|
| TMF620 | `Category`, `ProductCatalog` | not implemented | offerings are a flat list |
| TMF620 | `ImportJob`, `ExportJob` | not implemented | `make seed` (local only) |
| TMF622 | `CancelProductOrder` | excluded by decision (D1, D2, D4) | a `delete` item ends a product |
| TMF666 | `BillFormat`, `BillPresentationMedia` | not implemented | none |
| TMF666 | `BillingCycleSpecification` | not implemented | `billCycleDay` in the `BssBillingAccount` extension (`account.py:56-84`); decisions/tmf-api.md explains why `billStructure.cycleSpecification` does not fit |
| TMF666 | `FinancialAccount`, `PartyAccount`, `SettlementAccount` | not implemented | only `BillingAccount` |
| TMF676 | `Refund` | not implemented | ABR refunds (`appliedBillingCredit`) are credits on the account, not money paid back; there is no way to pay out a credit balance |
| TMF678 | `BillCycle` | not implemented | `billing_run` rows (internal API `/internal/v1/billingRun`), not linked from bills |
| TMF678 | `CustomerBillOnDemand` | not implemented (bound by D3) | none (`runType` is always `onCycle`, `bill.py:136`) |
| all | `Hub` / event subscription | excluded by decision (D1, D5) | none |

Sub-resources that exist as schemas but have no counterpart: TMF632 `ContactMedium`,
`PartyCharacteristic`, `IndividualIdentification`, `OrganizationIdentification`; TMF629
`CreditProfile`, `Characteristic`; TMF666 `Contact`, `PaymentPlan`, `TaxExemption`,
`AccountRelationship`; TMF622 `Note`, `OrderPrice`, `ProductOrderMilestone`,
`ProductOrderJeopardyAlert`; TMF637 `ProductPrice`, `ProductCharacteristic`,
`ProductRelationship`; TMF678 `TaxItem`, `AppliedBillingTaxRate`, `billDocument`
(`AttachmentRefOrValue`). All not implemented, none decided, except tax-related ones
(taxes out of scope, D4).

## 3. Attributes per implemented resource

How to read the tables:

- **(a) Mandatory in the spec, missing in our responses.** Every v5 resource schema
  requires only `@type`; TMF676 v4 `Payment` requires `account` and `paymentMethod`. All
  are always returned, and `tests/apiclient.py` validates every response of the suite
  against the spec schema for its path (decisions/tmf-api.md, "Contract tests"). So (a)
  is empty everywhere. The row lists the opposite case instead: **create (`_FVO`)
  attributes the spec makes optional and we require** (stricter than the spec), and
  create attributes the spec requires that we do not (more lenient).
- **(b) Accepted on input, ignored / not stored.**
- **(c) Stored, never returned** (database columns, `docs/db/`).
- **(d) Returned with a value that differs from what was sent.**
- **Refused:** spec attributes that are a 400 ("unknown attribute") because
  `TmfModel` forbids extras (`app/api/common.py:28`). Loud, not silent, but a TMF client
  that fills optional attributes gets a 400.

Cross-cutting, true for every resource below:

- (b) `@baseType` and `@schemaLocation` on POST/PATCH are accepted and dropped
  (`common.py:35-37`; proven by `test_meta_attributes_are_accepted_unknown_business_attributes_are_not`,
  which posts `@baseType: Entity` and gets 201). Only the billing account returns them,
  with our own values.
- (b) + (d) **`@type` on POST is never checked.** `check_type` (`common.py:153`) runs on
  PATCH only. `POST /individual` with `"@type": "Organization"` is stored as an
  individual and answered with `"@type": "Individual"`. Same for every POST. This is the
  pattern doc-findings #3 (role) and #17 (`@referredType`) fixed elsewhere by refusing the
  mismatch.
- (b) + (d) In references, `href` and `name` are accepted and ignored (`common.py:53-54`);
  the response states the stored name. Documented (decisions/tmf-api.md: "`href`, `name`,
  `@referredType` inside references" are always accepted).
- (b) **Query parameters on POST and PATCH are ignored**, `fields` included: the write
  handlers never read the query string (`deps.py:50-63`; only lists and GET-by-id check
  it, `listing.py`). The spec declares `fields` on POST and PATCH (`Fields` parameter in
  each spec's `paths`). `POST /customer?fields=name` returns the full body, and
  `POST /customer?anything=1` is not a 400.
- (d) Date-times go out in UTC (`common.py`, `utc`); `startDateTime` sent with `+03:00`
  comes back as the same instant with `Z`. Value-equal. Amounts like `19.9900` come back as
  `19.99`. Value-equal. Both documented.

### TMF632 individual / organization (`party.py`, `party_body` line 48)

| | Finding |
|---|---|
| (a) | Spec `Individual_FVO` requires `@type`, `givenName`, `familyName`; `Organization_FVO` requires `@type`, `name`. We require the business attributes, not `@type`. |
| (b) | none beyond the cross-cutting ones |
| (c) | `updated_at` (the spec's `Individual`/`Organization` have no `lastUpdate`; correct to omit) |
| (d) | the `@type` case above |
| Refused | everything else of the 30+ optional attributes, notably `contactMedium` (email, phone, address), `status` (`IndividualStateType`/`OrganizationStateType`), `externalReference`, `partyCharacteristic`, `birthDate`, `tradingName`, `isLegalEntity` |

### TMF629 customer (`customer.py`, `customer_body` line 54)

| | Finding |
|---|---|
| (a) | `Customer_FVO` requires `name`, `engagedParty`, `@type`: we require the first two. |
| (b) | engagedParty's `name`/`href` (cross-cutting) |
| (c) | The customer's billing accounts are known (`billing_account.customer_id`) but **`account[]` (`AccountRef`) is never returned**; a client cannot navigate customer -> accounts in TMF629. |
| (d) | none |
| **PATCH** | **`engagedParty` in a PATCH is a 400** (`customer.py:121`, `read_only=("engagedParty",)`), even when it repeats the current party. The spec's `PartyRole_MVO` (which `Customer_MVO` extends) has `required: [name, engagedParty]`, and its own merge-patch example `Customer_Update_example_Merge__request` sends `engagedParty`. A client that follows the spec's PATCH schema gets a 400 on every PATCH. Deviation by decision ("fixed at creation", decisions/tmf-api.md) for a *change*; refusing an unchanged repeat is not decided. |
| Refused | `description`, `role`, `validFor`, `statusReason`, `contactMedium`, `characteristic`, `account`, `agreement`, `paymentMethod`, `creditProfile`, `relatedParty`, `partyRoleSpecification` |

### TMF666 billingAccount (`account.py`, `account_body` line 147)

| | Finding |
|---|---|
| (a) | `BillingAccount_FVO` requires `name`, `relatedParty`, `@type`. We also require `billCycleDay` and `currency` (extension, by decision) and `relatedParty[0].role == "customer"` (422 otherwise). |
| (b) | `lastUpdate` on POST (documented, the spec's create schema lists it) |
| (c) | `opened_at` (no TMF attribute for it) |
| (d) | `@type: BillingAccount` on POST is answered with `@type: BssBillingAccount` - by decision, the extension pattern |
| Refused | `description`, `accountType`, `paymentStatus`, `billStructure`, `defaultPaymentMethod`, `financialAccount`, `contact`, `taxExemption`, `paymentPlan`, `accountRelationship`, `ratingType` |
| Note | `accountBalance` has one entry, `balanceType: currentBalance`. The spec's examples use `ReceivableBalance` and `DepositBalance` (TMF666 lines 6546, 6387); our value is ours, documented. `paymentStatus` (examples: `In Arrears`, `In Collection`) could be derived from overdue bills - not done; overdue is display only (decisions/bills.md). |

### TMF676 payment (`payment.py`, `payment_body` line 108)

| | Finding |
|---|---|
| (a) | `Payment_Create` requires `account`, `paymentMethod`, `totalAmount`: same as ours. |
| (b) | `paymentMethod` is reduced to one word (`payment.py:227`, `name or @type`): with both sent, `@type` is dropped. Every other `PaymentMethodRefOrValue` attribute is refused. `amount` (must equal `totalAmount`) and zero `taxAmount` are accepted and not stored separately. |
| (c) | none |
| (d) | **`paymentMethod` comes back as `{"name": X, "@type": X}`** (`payment.py:126`): send `{"name": "card", "@type": "CreditCard"}`, get `{"name": "card", "@type": "card"}`; send only `{"@type": "Cash"}`, get a `name` that was never sent. Documented in decisions/tmf-api.md ("both the stored word"), but it is a silent rewrite of `@type`. **`paymentItem[]` is not what was sent:** it is every allocation from the payment summed per bill and it grows later (documented). `status` is always `done`, `paymentDate`/`statusDate` the server clock. |
| Refused | `description`, `name`, `correlatorId`, `authorizationCode`, `channel`, `payer`, `paymentItem[].id` |

### TMF620 productSpecification / productOffering / productOfferingPrice (`catalog.py`)

| | Finding |
|---|---|
| (a) | `_FVO` requires `@type`, `lastUpdate`, `lifecycleStatus`, `name` (+ `priceType` for a price). We accept `lastUpdate` and ignore it. We additionally require, against the spec: offering `validFor` and `productSpecification`; price `price` and `validFor`, and `recurringChargePeriodType` for a recurring price. |
| (b) | `lastUpdate` on POST (documented) |
| (c) | price -> offering link (`product_offering_id`): the spec's `ProductOfferingPrice` has no back-reference, correct to omit |
| (d) | `productOfferingPrice[]` on an offering comes back ordered by `lower(valid_for)` (`repository/catalog.py:161`), not in the order sent. |
| Refused | spec: `version`, `brand`, `isBundle`, `category`, `productSpecCharacteristic`, `attachment`, `validFor` on a specification; `statusReason`, `version`, `category`, `channel`, `productOfferingTerm`, `bundledProductOffering`, `prodSpecCharValueUse`, `agreement`, `marketSegment` on an offering; `description`, `unitOfMeasure`, `percentage`, `tax`, `popRelationship`, `externalIdentifier` on a price. Inline prices in `productOfferingPrice[]` (spec: `ProductOfferingPriceRefOrValue`) are a 422 - deviation by decision (decisions/catalog.md). |
| Values | `lifecycleStatus`, `priceType`, `recurringChargePeriodType` are free strings in the spec; we accept `In Design`/`Active`/`Retired`, `recurring`/`oneTime`, `monthly` with length 1. The spec's `priceType` description also names `discount`, `allowance`, `penalty` (TMF620 line 5418). |

### TMF622 productOrder (`ordering.py`, `order_body` line 134)

| | Finding |
|---|---|
| (a) | `ProductOrder_FVO` requires `@type`, `productOrderItem`; `ProductOrderItem_FVO` requires `@type`, `action`, `id`. We also require `billingAccount` (by decision, "mandatory - ours"). |
| (b) | **An item's `billingAccount`** (`ordering.py:61`) is compared with the order's (`billingAccountMismatch`) and then dropped: there is no column for it (`alembic/versions/0001_initial_schema.py`, `product_order_item`), and the item in the response has none. `quantity` (only `1` accepted) is not stored. On a `modify`/`noChange` item, `product` is dropped (`ordering.py:86`) - those items are always rejected. |
| (c) | none of note (`updated_at`) |
| (d) | **Items come back sorted by `item_id` as text** (`repository/orders.py:67` and `:114`, `ORDER BY item_id`): items sent as `"1"`, `"2"`, `"10"` come back `"1"`, `"10"`, `"2"`. `requestedStartDate` is normalized to midnight in `BILLING_TZ` (documented). A rejected order is a 201 with `state: rejected` (documented). |
| Missing | `orderTotalPrice`, `itemPrice`, `itemTotalPrice`: the price is looked up at ordering time (`noRecurringPrice`, `priceEnds`) but never shown. |
| Refused | `description`, `category`, `priority`, `externalId`, `note`, `relatedParty`, `channel`, `agreement`, `quote`, `productOfferingQualification`, `requestedCompletionDate`, `requestedInitialState`, `payment`, `orderRelationship`; item `itemTerm`, `appointment`, `productOrderItemRelationship`, nested `productOrderItem` |

### TMF637 product (`inventory.py`, `product_body` line 59)

| | Finding |
|---|---|
| (a) | read-only resource; nothing to send |
| (c) | The offering's **`productSpecification`** is known but not returned. The **price** the product is billed at is known (internal `/internal/v1/billingAccount/{id}/products` returns it) but `productPrice[]` is not returned. `orderDate` is not returned although the creating order's date is used as `creationDate`. `relatedParty` (the customer, via the account) is not returned. |
| (d) | `name` is the offering's name (no own name). |

### TMF678 customerBill / appliedCustomerBillingRate (`bill.py`)

| | Finding |
|---|---|
| (a) | read-only except PATCH |
| (b) | none |
| (c) | **`billing_run_id`** (bill and ABR) - the spec's `billCycle` (`BillCycleRef`) is the natural place, but `BillCycle` is not exposed. `refunds_rate_id` (which charge an ABR refund reverses) is not returned. The bill's state history (`customer_bill_state_change`) is internal only (`/internal/v1/customerBill/{id}`). |
| (d) | none |
| Missing | `nextBillDate` (derivable from `billCycleDay`), `taxExcludedAmount`/`taxIncludedAmount` (both would equal `amountDue` with taxes out of scope), `billCycle`, `relatedParty`, `paymentMethod`, `billDocument`; on an ABR `name`, `description`, `taxIncludedAmount`. |
| PATCH | `CustomerBill_MVO` allows `state` and `billCycle`; we take `state` only, `billCycle` is a 400. The comment at `bill.py:64` ("Every other attribute of the spec's CustomerBill: present, but not changed by a PATCH") reads as if they were returned; most are present only in the spec's schema, never in our responses. |

## 4. Lifecycle and state enums

The five TMF-derived Postgres enums equal the spec enums (`tests/test_enum_lock.py`). What
is *reached* is a subset.

| Resource / attribute | Spec values | We accept on input | Actually reached | Transitions we enforce |
|---|---|---|---|---|
| product `status` (TMF637 `ProductStatusType`) | created, pendingActive, cancelled, active, pendingTerminate, terminated, suspended, aborted | read-only | created, active, pendingTerminate, terminated, cancelled | written by orders and the worker's flips only (decisions/tmf-api.md, "Status flips"). **Never reached: `pendingActive`, `suspended`, `aborted`** - though the billing code handles them (`billing/core.py:37`, `repository/products.py:158`). The spec has no state machine for product in the vendored file (enum only). |
| productOrder `state` (TMF622 `ProductOrderStateType`, 13 values) | acknowledged ... inProgress.accepted | read-only (`requestedInitialState` refused) | completed, rejected (acknowledged and inProgress only inside the request's transaction) | `acknowledged -> inProgress -> completed`, `acknowledged -> rejected` (`ordering/states.py`). Never: pending, held, cancelled, failed, partial, assessingCancellation, pendingCancellation, draft, inProgress.accepted. |
| productOrderItem `state` (11 values) | | read-only | completed, rejected, cancelled | as above, plus `acknowledged -> cancelled` |
| item `action` (`ItemActionType`) | add, modify, delete, noChange | all four | add and delete executed; modify/noChange always rejected (`actionNotSupported`, D4) | - |
| customerBill `state` (TMF678 `CustomerBillStateType`) | new, onHold, validated, sent, settled, partiallyPaid | PATCH: `onHold`, and from onHold only the state the allocations imply | all six, but new and validated only within the run's transaction (and bills left `new` before migration 0006) | decisions/bills.md; the spec file has no transition model. |
| customerBill `runType` (`CustomerBillRunType`) | onCycle, offCycle | read-only | onCycle only (`bill.py:136`) | - |
| customer `status` (free string; examples `Created`, `CreditCheckDone`) | - | Created, Active, Inactive | all three | **none**: any to any. Ordering never looks at it (`ordering/process.py` checks only the account's state, line 140) - an `Inactive` customer's account can still order. |
| billingAccount `state` (free string; examples `Active`, `Suspended`, `Inactive`, `inactive`) | - | Active, Suspended, Closed | all three | **none**: `Closed -> Active` is allowed. Billing ignores state (suspension semantics open, decisions/billing-core.md). Ordering rejects *every* order on a non-Active account (`accountNotActive`, `process.py:140`), delete items included - a product on a `Suspended` or `Closed` account cannot be terminated by an order, and keeps being billed. |
| catalog `lifecycleStatus` (free string; example `In Design`) | - | In Design, Active, Retired | all three | forward only, Retired final (decisions/catalog.md) |
| payment `status` (free string; `PaymentStatusExampleType`) | pendingAuthorization ... done | read-only | done only | - |
| individual/organization `status` (`IndividualStateType`, `OrganizationStateType`) | initialized, validated, deceased / closed | refused (400) | - | - |

## 5. TMF630 guideline features

| Feature | What the vendored specs encode | What we do | Gap |
|---|---|---|---|
| Attribute selection | `fields` query parameter on every GET, POST and PATCH ("Comma-separated properties to be provided in response") | `fields` on lists and GET-by-id, top-level names only; `id`, `href`, `@type` always; unknown name 400 (`listing.py`) | ignored on POST/PATCH (section 3). Dotted paths (`engagedParty.name`) are a 400; whether TMF630 allows them is not verifiable from `spec/`. |
| Filtering | not declared as parameters in any vendored spec (only `fields`, `offset`, `limit`) | equality on a per-resource list; any other parameter 400 (`listing.py:73`) | **A comma-separated value silently matches nothing**: `?status=active,terminated` is compared as one string (`as_str`, then `column::text = %s`, `repository/_sql.py:66`) and answers 200 `[]`. TMF630's operators (`.gt`, `.in`, ...) and comma-OR are not verifiable from `spec/`; an operator suffix is a loud 400 here, the comma is not. No filter on dates or amounts (e.g. bills by `billDate`, payments by `paymentDate`). |
| Sorting | no `sort` parameter in any vendored spec | creation order (`_sql.py`, `ORDER BY created_at, id`); `sort=` is a 400 | not verifiable from `spec/`; a 400 is the honest answer |
| Pagination | `offset`, `limit`; 200 response headers `X-Total-Count`, `X-Result-Count` (all eight specs) | both, plus both headers; limit default 20, max 100, above max 400; never 206 | conforms. The 400 above 100 (instead of a capped page) is ours. |
| PATCH formats | v5: `application/json`, `application/merge-patch+json`, `application/json-patch+json`, `application/json-patch-query+json` (every PATCH in the seven v5 specs) | merge-patch only, 415 otherwise (`deps.py:57`; `test_patch_accepts_only_merge_patch_json` locks in 415 for `application/json`) | **deviation by decision (D6)**. `application/json` is in every v5 spec and is the content type a generic client sends by default. JSON Patch (RFC 6902) and JSON-Patch-Query are absent. |
| Error format | v5 `Error`: required `@type`, `code`, `reason`; v4: `code`, `reason` | `@type`, `code`, `reason`, `message`, `status` on every non-2xx (`errors.py`) | conforms. `code` is the HTTP status (`"400"`), not an application code; `referenceError` never set. 415 and 422 are not declared in the specs (documented). |
| Polymorphism | `@type` required on every v5 resource and `_FVO`/`_MVO`; `@baseType`, `@schemaLocation` optional; discriminator mappings on `@type` | `@type` returned everywhere; checked (not changeable) on PATCH; extension pattern with a served schema for the billing account | **`@type` on POST unchecked and silently replaced; `@baseType`/`@schemaLocation` on input dropped** (section 3). The contract tests do not dispatch on discriminators next to `allOf` (documented limit). |
| POST response | 201 (and 202) | 201 + `Location` | conforms |

## 6. Notifications

Every vendored spec defines the hub model: `POST /hub` (register a callback), `DELETE
/hub/{id}` (TMF629 also `GET /hub/{id}`), and `/listener/<event>` operations - 92 in all -
that describe the **client's** callback endpoints, not ours. A server that supports
notifications implements the hub and POSTs events to registered callbacks.

| API | Events defined (listener paths) |
|---|---|
| TMF620 | create / attributeValueChange / stateChange / delete for productSpecification, productOffering, productOfferingPrice, category, productCatalog; create / stateChange for importJob, exportJob (24) |
| TMF622 | productOrder create, attributeValueChange, stateChange, delete, informationRequired, errorMessage, jeopardyAlert, milestone; cancelProductOrder create, stateChange, informationRequired (11) |
| TMF629 | customer create, attributeValueChange, stateChange, delete (4) |
| TMF632 | individual and organization: create, attributeValueChange, stateChange, delete (8) |
| TMF637 | product create, attributeValueChange, stateChange, delete, `productProductBatchEvent` (5) |
| TMF666 | create, attributeValueChange, stateChange, delete for each of the 7 account resources (28) |
| TMF676 | payment and refund: create, attributeValueChange, stateChange, delete (8) |
| TMF678 | customerBill create, stateChange; customerBillOnDemand create, stateChange (4) |

**We emit none**: there is no hub route and nothing in `app/` publishes events (`grep -i
'hub\|listener\|event' app/` finds only a `threading.Event`). Excluded by decision (D1,
D5). For a billing demo the events with the most value would be `customerBillCreateEvent`
and `customerBillStateChangeEvent` (written by the worker, and by payments/PATCH),
`paymentCreateEvent`, `productStateChangeEvent` (the worker's flips) and
`productOrderStateChangeEvent`. Effort is L: subscriptions table, an outbox written in the
same transaction as the change (the worker's run included), a delivery loop with retries,
and the callback URL as an outbound-request surface on a VPS that also runs a VPN.

## 7. TMF APIs not implemented that a billing demo would typically include

The vendored specs already reference several of these (a `...Ref` schema or example hrefs),
which is cited where it applies. API numbers marked † are not named anywhere in `spec/`;
they are given from general knowledge and not verified here.

| API | What it would add to this demo |
|---|---|
| Usage Management (TMF635 †) | usage-based charges next to the monthly recurring ones; the billing core prices only recurring prices today |
| Prepay Balance Management (TMF654 †) | prepaid buckets and top-ups; our balance is a postpaid ledger (`accountBalance`) |
| Payment Methods (TMF670 †) | stored methods per customer/account; referenced as `PaymentMethodRef` in `Customer.paymentMethod`, `BillingAccount.defaultPaymentMethod`, `CustomerBill.paymentMethod`, and as `tmf-api/paymentMethods/v5` in 18 TMF666 example hrefs. Would replace the one-word `paymentMethod` on payments. |
| Agreement Management (†) | contracts and terms behind an offering or customer; referenced as `AgreementRef` (`Customer.agreement`, `ProductOrder.agreement`) and `tmf-api/agreementManagement/v5` in TMF620 examples |
| Quote Management (TMF648 †) | a priced proposal before the order; referenced as `QuoteRef` in `ProductOrder.quote` |
| Shopping Cart (TMF663 †) | a basket that becomes an order; useful for a self-service front, little for billing |
| Product Offering Qualification (TMF679 †) | "can this account order this offering" before ordering - our ordering rejections (`offeringNotSellable`, `priceEnds`, ...) as a query; referenced as `ProductOfferingQualificationRef` in TMF622 |
| Party Role Management (TMF669 †) | roles other than customer (bill receiver, payer, contact); TMF666 examples use roles `owner`, `bill receiver`, `employee` |
| Communication Management (TMF681 †) | bill and payment notices to the customer; pairs with `paymentDueDate` |
| Event Management (TMF688 †) | a central event bus instead of per-API hubs (section 6) |
| Document Management (TMF667 †) | the bill as a document (`CustomerBill.billDocument`); `tmf-api/documentManagement/v5` appears in TMF620 examples |

## 8. Prioritized summary

### Conformance correctness (our existing APIs are wrong or surprising to a TMF client)

| # | Gap | Where | Value | Effort |
|---|---|---|---|---|
| C1 | `@type` on POST is unchecked and silently replaced; `@baseType`/`@schemaLocation` dropped | `common.py:28`, `:153` | high (same class as doc-findings #3, #17) | S |
| C2 | A comma-separated filter value silently returns an empty list | `listing.py`, `_sql.py:66` | high (wrong answer, not an error; MCP clients try it) | S |
| C3 | PATCH customer refuses an unchanged `engagedParty`, which the spec's PATCH schema requires and its example sends | `customer.py:121` | medium | S |
| C4 | PATCH with `Content-Type: application/json` is a 415 though every v5 spec lists it | `deps.py:57` (D6) | medium (generic clients) | S, but changes a recorded decision |
| C5 | `paymentMethod.@type` rewritten to the method's name | `payment.py:126`, `:227` | medium | S (refuse a mismatch) / M (store both: migration) |
| C6 | Query parameters (`fields` included) silently ignored on POST/PATCH | `deps.py:50-63` | low-medium | S |
| C7 | Order items returned in text order of `item_id`, not as sent | `repository/orders.py:67`, `:114` | low-medium | M (needs a position column: migration) |
| C8 | Item-level `billingAccount` accepted, not stored or returned | `ordering.py:61` | low | S |
| C9 | A product on a non-Active account cannot be terminated by an order and keeps billing | `process.py:140` | medium (domain) | S-M, needs a decision (suspension semantics are open) |

### Breadth (new APIs, resources or attributes)

| # | Gap | Value for a billing demo | Effort |
|---|---|---|---|
| B1 | Prices in TMF: `Product.productPrice[]`, `ProductOrder.orderTotalPrice` / `itemPrice` | high: today a TMF client cannot see what a product costs | M |
| B2 | Navigation we already know: `Customer.account[]`, `Product.productSpecification`, `Product.relatedParty`, `Product.orderDate` | medium | S |
| B3 | Bill cycle: `GET /billCycle` from `billing_run`, `CustomerBill.billCycle`, `nextBillDate`, `taxExcludedAmount`/`taxIncludedAmount` (= `amountDue`) | medium-high | M |
| B4 | Event notifications: hub + bill/payment/product/order events | high (live demo) but excluded (D1, D5) | L |
| B5 | TMF676 `refund`: pay out a credit balance | medium | M-L (balance semantics, settlement) |
| B6 | `cancelProductOrder` | medium; excluded (D1, D4) | M |
| B7 | Party `contactMedium` (email/phone) | medium (bill notices, UI) | M (new table) |
| B8 | `customerBillOnDemand` (an off-cycle bill, e.g. a final bill) | medium | L (must go through the run, D3) |
| B9 | TMF620 `category` / `productCatalog` | low for billing | M |
| B10 | Usage (TMF635 †) | high, but a new billing capability | L |

### Top 10 by value vs effort, with a suggested slice order

| Rank | Item | Kind | Effort | Slice |
|---|---|---|---|---|
| 1 | C2 comma filter -> 400 (or implement OR) | correctness | S | 1 "input strictness" |
| 2 | C1 `@type` checked on POST; refuse a foreign `@type` | correctness | S | 1 |
| 3 | C6 query parameters on POST/PATCH: `fields` honoured, others 400 | correctness | S | 1 |
| 4 | C3 PATCH customer accepts an unchanged `engagedParty` | correctness | S | 2 "PATCH conformance" |
| 5 | C4 PATCH accepts `application/json` as merge patch (decision change) | correctness | S | 2 |
| 6 | C5 `paymentMethod` without the `@type` rewrite | correctness | S | 3 "payments" |
| 7 | B2 navigation attributes we already have | breadth | S | 4 "derived read attributes" |
| 8 | B1 product and order prices | breadth | M | 4 |
| 9 | B3 bill cycle and bill amounts | breadth | M | 5 "bill cycle" |
| 10 | C7 order items in submission order | correctness | M | 6 (migration) |

After these: B4 (events) as its own design task with a decision change, then B5 refund.
C9 belongs to the open "suspension semantics" question (decisions/billing-core.md), not
to a conformance slice.

## 9. MCP note

The connector's tools are generated from the app's own routes (`bss_mcp/tools.py`,
decisions/mcp.md "Slice 3"), and list filters are probed from the handler's 400. So every
gap in the API is a gap in the tools, one to one:

- **Missing operations are missing tools**: no tool for anything marked "not implemented"
  or "excluded" in section 1 (no cancel order, no refund, no bill cycle, no category, no
  subscriptions).
- **Filters are equality only** (`filters` object with `additionalProperties: false`,
  `tools.py:176`): no date or amount ranges, no "bills of this customer" (only by
  `billingAccount.id`), no customer -> accounts navigation (B2). An agent needs several
  calls where one filter would do.
- **C2 hits agents hardest**: a model that passes `"status": "active,created"` gets an
  empty list and a plausible-looking "no products" answer, not an error.
- **C1** lets an agent create a resource with a wrong `@type` without noticing.
- `totalCount`/`resultCount` are surfaced from the headers (`tools.py:114`), so paging
  works. PATCH tools send merge patch, so C4 does not affect them.
- Missing response attributes (B1 prices, B3 `nextBillDate`) are visible to an agent only
  through the internal tools (`list` of products with prices, settlement), not through
  the TMF ones.

## 10. Code vs recorded decisions

No hard contradiction found: the filters, read-only rules, 405/404 behaviour, lifecycle
rules, `@referredType` check and mappings in decisions/tmf-api.md and decisions/catalog.md
match the code. Tensions worth a decision:

1. decisions/tmf-api.md: "**Unknown attributes are rejected (400), never silently
   dropped.**" The rule is about *unknown* attributes, and the code keeps it. But
   *known* attributes are silently dropped or rewritten in several places: `@type` on
   POST (C1), `paymentMethod.@type` (C5), item `billingAccount` (C8), `@baseType` /
   `@schemaLocation`. doc-findings #3 and #17 treated the same pattern as bugs.
2. decisions/tmf-api.md, "Lists": "any other query parameter is a 400" - true for lists;
   POST and PATCH ignore their query string entirely (C6). Not covered by the wording.
3. decisions/data-model.md: free-string fields take the spelling of the spec's examples.
   `balanceType: currentBalance` is not one of the spec's examples (`ReceivableBalance`,
   `DepositBalance`); the rule is about enums, so this is a reading question, not a
   violation.
4. `app/api/bill.py:64`: the comment on `BILL_READ_ONLY` calls those attributes
   "present"; most appear only in the spec's schema, never in our responses (wording, not
   behaviour).
