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
contradicting `@referredType` is a 422), answered as a `PartyRef`. The owner is fixed at
creation; `relatedParty` in a PATCH is a 400.

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
`accountBalance` input and every other spec attribute are refused (400). Filters: `name`,
`state`, `accountType`, `relatedParty.id`.

**Closing** a financial account that still contains accounts is allowed; the links stay
and are still summed. Only new links to it are refused.

**UI**: read-only. A *Financial accounts* screen (sidebar, no count - one more request on
every screen for a rarely used list) lists owner, state, the balance per currency and the
contained accounts, billing accounts linking to their subscriber card. The subscriber
card names the account's financial account next to its BillingAccount id. Creating and
linking stay in the API and the MCP tools: UI writes would need the pinned request
contract (frontend.md) for a feature the demo does not need on screen.

**Migration `0013`**: table `financial_account` (owner `party_id`, `UNIQUE (id,
party_id)` for slice 2's composite foreign key), `billing_account.financial_account_id`
(nullable, indexed; existing accounts stay unlinked) and the owner trigger. Rollback:
`alembic downgrade 0012` drops the links and the table.

## Party and settlement accounts (slice 2)

TMF666's `SettlementAccount` is a specialization of `PartyAccount` with the same
attributes. Both are **records only**: accounts of a party - a partner, a supplier - that
this BSS does not bill and holds no ledger for. They have no `accountBalance` (sending one
is a 400): a zero would be an invented number. Billing and the worker never read them.

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
`taxExemption` and `accountRelationship` are refused (400). Filters: `name`, `state`,
`accountType`, `relatedParty.id`, `financialAccount.id`.

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
