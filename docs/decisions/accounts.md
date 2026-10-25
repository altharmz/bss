# TMF666 financial, party and settlement accounts (2026-10-16)

Approved as two slices: financial accounts with the billing account link (4 operations,
migration `0013`), then party and settlement accounts (8 operations, migration `0014`).
Together they complete TMF666's list/retrieve/create/PATCH surface except the two
`billingCycleSpecification` writes. Coverage after slice 1: 71/129 non-listener TMF
operations (55.0%); after slice 2: 79/129 (61.2%). This is endpoint coverage, not full
conformance.

## Financial accounts (slice 1)

TMF666: a financial account "aggregates the amounts of one or more party accounts
(billing or settlement) owned by a given party". So its owner is a **party**, not a
customer: exactly one `relatedParty` with role `owner` (any other or no role is a 422,
nothing stored), `partyOrPartyRole` an existing Individual or Organization (a
contradicting `@referredType` is a 422), answered as a `PartyRef`. The owner was fixed
at creation (`relatedParty` in a PATCH a 400) until slice 5: parties in other roles are
taken, and the owner changes while no account links
([account attributes](#account-attributes-2026-10-18)).

**The link lives on the account, never on the financial account.** A billing account's
`financialAccount` (`FinancialAccountRef`) is accepted on POST and PATCH; PATCH `null`
unlinks. It must exist (422), be owned by the billing account's owner - the
`engagedParty` of its customer (422 otherwise) - and not be `Closed`, unless the link is
unchanged (a repeat of the current link after a close is a 200). The financial account
row is read `FOR SHARE` while linking, so a concurrent close waits for the link to commit.
A database trigger (`billing_account_financial_owner`) refuses a link across owners
whatever writes it. `GET /billingAccount?financialAccount.id=` filters by it.

On the financial account, the link is answered as the derived, read-only
`accountRelationship[]`: `{relationshipType: contains, account: AccountRef}` per linked
account, in creation order (`@referredType: BillingAccount`). Sending
`accountRelationship` is a 400 on POST and PATCH: one place to write a link, no second
source of truth to keep in step.

**Balance**: `accountBalance[]` has one `currentBalance` entry per currency of the linked
billing accounts, ordered by currency: the sum of their balances (the billing account
formula, positive = in credit). No links: an empty list. No currency extension is needed,
and billing accounts in different currencies can share a financial account. Read-only
(400 on input). Billing and the worker never look at financial accounts.

**Other attributes.** `name` (required, nonempty), `description`, `accountType` (free,
nonempty), `state` (`Active` default, `Suspended`, `Closed` - the billing account's enum,
no transition rules, as there), `creditLimit` (Money in its own currency, never negative,
informational, PATCH `null` removes), `lastUpdate` (from `updated_at`; accepted and
ignored on POST like a billing account's, 400 on PATCH). `contact`, `taxExemption`,
`accountBalance` input and every other spec attribute were refused (400); `contact` and
`taxExemption` are documents since slice 5. Filters: `name`,
`state`, `accountType`, `relatedParty.id`.

**Closing** a financial account that still contains accounts is allowed; the links stay
and are still summed. Only new links to it are refused.

**UI**: a *Financial accounts* screen lists owner, state, the balance per currency and the
contained accounts, billing accounts linking to their subscriber card. The subscriber card names
the account's financial account next to its BillingAccount id. **Superseded 2026-10-25 (slice
16):** the screen creates, edits and deletes financial accounts, the subscriber Edit form links
and unlinks one, and the card deletes an account without history (entry below).

Since 2026-10-20, the sidebar shows the financial account total like the other lists,
using `financialAccount?limit=1&fields=id` and `X-Total-Count`. This adds one small
request when the sidebar mounts; loading or failed counts stay blank.
**Superseded 2026-10-21:** the item moves to the sidebar's Billing section without a
count ([frontend.md](frontend.md#sidebar-grouped-by-tmf-area-global-orders-screen-2026-10-21)).

**Migration `0013`**: table `financial_account` (owner `party_id`, `UNIQUE (id,
party_id)` for slice 2's composite foreign key), `billing_account.financial_account_id`
(nullable, indexed; existing accounts stay unlinked) and the owner trigger. Rollback:
`alembic downgrade 0012` drops the links and the table.

## Party and settlement accounts (slice 2)

TMF666's `SettlementAccount` is a specialization of `PartyAccount` with the same
attributes. Both are **records only**: accounts of a party - a partner, a supplier - that
this BSS does not bill and holds no ledger for. They have no `accountBalance` (since
slice 5 it is answered as `[]`, not a zero entry): a zero would be an invented number.
Billing and the worker never read them.

**Same rules as a financial account** for the owner (`relatedParty` role `owner`, an
Individual or Organization, fixed), `name`, `description`, `accountType`, `state`,
`creditLimit` and `lastUpdate`. `financialAccount` is accepted on POST and PATCH (`null`
unlinks) under the billing account's rules: it must exist, have the **same owner** (the
account's own party), and not be `Closed` unless unchanged. The owners are equal by
construction in the database too: a composite foreign key `(financial_account_id,
party_id) -> financial_account (id, party_id)`. A linked party or settlement account
appears in its financial account's `accountRelationship[]` with its own `@referredType`
and href, ordered with the billing accounts by creation; it adds nothing to the balance.
`billStructure`, `paymentPlan`, `paymentStatus`, `defaultPaymentMethod`, `contact`,
`taxExemption` and `accountRelationship` were refused (400) - taken since slice 5, and
the owner is no longer fixed ([account attributes](#account-attributes-2026-10-18)).
Filters: `name`, `state`, `accountType`, `relatedParty.id`, `financialAccount.id`.

**No polymorphic lists (deviation by decision).** Each resource has its own path and
`@type`: `/partyAccount` lists only `PartyAccount`s - not settlement or billing accounts,
which the spec's inheritance would allow - and `/settlementAccount` only
`SettlementAccount`s. A GET or PATCH of one resource's id on the other's path is a 404; a
POST or PATCH naming the other `@type` is a 400. A list mixing resources would answer ids
that belong to other paths, and the billing account would need two shapes.

**UI**: the Financial accounts screen lists contained party and settlement accounts by
name and kind; they have no screen of their own, so they are not links.

**Migration `0014`**: enum `party_account_kind` (`PartyAccount`, `SettlementAccount` - ours,
equal to the `@type`) and table `party_account` with `kind`, the same columns as
`financial_account` and the composite foreign key. Rollback: `alembic downgrade 0013`
drops the table and the enum (their data is lost; financial accounts stay).

## Account attributes (2026-10-18)

Slice 5 of ["Following TMF"](tmf-api.md#following-tmf-2026-10-17), the plan approved by
the human (2026-10-18, "go"; the four choices "based on TMF recommendations", read from
the spec's `_FVO`/`_MVO` schemas and its own examples). Migration `0023`. 101 lines left
`tests/tmf-gaps.txt` (476 -> 375): every attribute of `BillingAccount`,
`FinancialAccount`, `PartyAccount`, `SettlementAccount` and `BillingCycleSpecification`
is taken on POST and PATCH and returned; TMF666 has no gap lines left. The three
`billingAccount.ratingType` lines of TMF622, TMF637 and TMF678 closed with it.

- **`relatedParty` is a list of parties in any role**, as the spec's examples have it
  (owner, bill receiver, contact, service provider, employee; its own JSON Patch example
  adds a `contact` to a party account). Exactly one entry has the account's **anchor
  role** - `customer` on a billing account, `owner` on the others - and is resolved to
  our customer or party as before (422 if it does not exist, a contradicting
  `@referredType` is a 422) and answered resolved (href, name). The others are stored as
  sent, in the order sent, checked against `RelatedPartyRefOrPartyRoleRef_FVO`/`_MVO`
  (their references are not resolved, as in slice 4). The anchor stays required -
  billing cannot work without a customer, and the spec's own create examples (a billing
  account with only a `service provider`) are refused for that reason. The anchor entry
  is still validated by our model, so `partyOrPartyRole` needs no `@type` there, while
  the other entries follow the spec (`@type` `PartyRef`/`PartyRoleRef`). A missing or
  second anchor is a 422 naming the roles sent. Accounts stored before `0023` answer their
  anchor alone. The UI finds the customer/owner by role, not position.
- **The anchor changes only while nothing depends on it** (409 otherwise; the rule of
  slice 1's `engagedParty`): a billing account's customer while the account has no
  history (`parties.ACCOUNT_HISTORY`, what DELETE asks; writers that add history lock the
  account, as the PATCH does), a financial account's owner while no account links to it
  (the PATCH takes it FOR UPDATE, links take it FOR SHARE). A billing, party or
  settlement account's link to a financial account must stay with the same owner: a new
  owner with an unchanged link to the old owner's financial account is a 422 - unlink it
  (`financialAccount: null`) or link the new owner's in the same request. (The database
  refuses it too: the owner trigger and the composite foreign key; the check makes it a
  422 instead of a 500.)
- **Documents**: `contact`, `taxExemption`, `paymentPlan`, `defaultPaymentMethod`,
  `paymentStatus`, `accountRelationship` (billing, party, settlement accounts),
  `billStructure` (party and settlement accounts: a record, they are never billed),
  `contact` and `taxExemption` (financial accounts), and a cycle specification's
  `validFor` - stored as sent in a `details` jsonb, checked against the spec at every
  depth (`app/api/specschema.py`), merge patch replaces one whole and `null` removes it.
  Billing reads none of them except a billing account's `taxExemption`: since slice 7
  its certificates exempt the account from a tax type ([billing-core.md](billing-core.md#taxes-2026-10-18)).
  A billing account's `description` and `accountType` are columns, as on the other
  accounts (`accountType` is a list filter).
- **`paymentStatus` is a record**, not derived: both `_FVO` and `_MVO` let a client write
  it, and the spec's examples (`In Arrears`, `In Collection`) name collection states that
  bills cannot tell. Overdue stays display only (bills.md).
- **`ratingType` is `postpaid` (default) or `prepaid`** (since P5a, see
  [prepaid.md](prepaid.md#p5a-enable-prepaid-accounts-2026-10-23)); `null` and other
  values are a 422. A `BillingAccountRef.ratingType` in an order, a product or an
  on-demand bill is accepted only when it equals the account's mode
  (`RatedBillingAccountRef`, `account.check_rated`); TMF676's v4 `AccountRef` has none.
- **`billCycleDay` and `currency` are optional on POST** (`BillingAccount_FVO` requires
  only `name`, `relatedParty`, `@type`). `billCycleDay` defaults to the day of the
  creation date in BILLING_TZ, 29-31 as 28 (anniversary billing); `currency` to the
  `creditLimit`'s unit if one is sent, else `UI_CURRENCY` - that setting is now also the
  server's default currency (no compose change). Both stay in the `BssBillingAccount`
  extension and fixed after creation. Not chosen: deriving the cycle day from
  `billStructure.cycleSpecification` (the spec's example "billingDateShift 20 = the cycle
  for the 20th"): our periods start on `billCycleDay`, not on the 1st.
- **Party and settlement accounts answer `accountBalance: []`** (no ledger); a client may
  send exactly that.
- **Billing cycle specification offsets** are the policy's, from the period start (= the
  bill date, `billingDateShift` 0): `chargeDateOffset` and `creditDateOffset` 0 (a bill of
  D collects the ABRs of runs up to D, billing-core.md), `mailingDateOffset` 0 (issued on
  D), `paymentDueDateOffset` = `BILL_PAYMENT_TERM_DAYS`. Returned on the built-in and
  every stored specification, accepted when sent equal, a 422 otherwise - not stored, so
  a change of the setting changes every specification's answer, as it changes new bills.
- **MCP:** the request models carry `relatedParty` and the documents as untyped fields
  with descriptions; the tool texts say what changes when.

Tests: `tests/test_api_account_attributes.py` (the spec attributes locked to the
documents, round trips, defaults, ratingType in orders, anchor changes and their 409/422,
the spec's JSON Patch example, cycle offsets), `test_0023_account_details`, the
FinancialAccounts UI test (owner after a contact), the gap lock. Negative proofs: without
the history check a customer change after an order answers 200; without the link check
an owner change answers 500 (the database's refusal); without the payment term check a
specification with another `paymentDueDateOffset` is stored; answering the stored
`relatedParty` without resolving the anchor fails the round trips; the UI taking
`relatedParty[0]` as the owner shows the contact. Rolling back `0023` needs
`alembic downgrade 0022` by hand (drops `details` on four tables and the billing
account's `description`/`account_type`).

## UI: financial account writes, linking and billing account delete (slice 16, 2026-10-25)

Built as designed ([next-slices-design.md](../next-slices-design.md) section 16), frontend only;
it replaces "UI: read-only" above. `api/financial.ts`, `screens/FinancialForms.tsx`.

- **Financial accounts screen:** `ui.financialAccounts.new` opens the form (`.form`): name, owner
  (select of individuals and organizations, relatedParty role `owner`), account type, description,
  informational credit limit with its currency (exact decimal token), state. Per row,
  `ui.financialAccounts.edit` PATCHes only the changed fields (a cleared optional field is `null`;
  an owner change sends the owner plus the other related parties as loaded) and
  `ui.financialAccounts.delete` (confirmation `.confirmDelete`) is shown only when
  `accountRelationship` is empty. The server's 409/422 text shows in the dialog with the values kept;
  after an uncertain result the dialog offers Refresh.
- **Linking:** the subscriber Edit form has a **Financial account** select
  (`ui.subscriber.financialLink`) from `financialAccount?relatedParty.id={party}` plus None; it
  sends `financialAccount` (a `FinancialAccountRef`) or `null` in the billing account PATCH.
- **Delete subscriber** (`ui.subscriber.delete`, confirmation `.deleteConfirm`): shown for a
  postpaid account when the products, charges, transactions and bills the card loaded are all
  empty. DELETE with no body, then the Subscribers list. A 409 shows "... - close it instead".
- Tests: `FinancialAccounts.test.tsx` (create, edit, owner refusal, delete availability and
  body), `SubscriberCard.test.tsx` (link, unlink, 422, delete offered/hidden per resource, prepaid,
  409) and `tests/test_ui_requests.py::test_financial_account_writes_and_links_as_the_ui_sends_them`.
- Deviations: Delete is a header button, not a menu item (the header has no menu); the card now
  loads charges and transactions itself and passes them to the history panels (same requests).
  The existing `editBackend` test helper stubs the new financial-accounts list (TEST-CHANGE).

