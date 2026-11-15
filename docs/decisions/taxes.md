# Tax engine and service address (issue #184, epic #182)

Prepaid charges are taxed by a tax engine for the line's place of primary use, instead of
the flat rate on the price. The engine is a placeholder today: `BSS_TAX_ADAPTER=stub`
(default; an unknown value fails startup, `app/config.py`). The interface and the stub are
in `app/integrations/tax/`; a real Avalara-style integration is a later issue (#203) and
keeps this contract. Postpaid keeps the flat price tax ([billing-core.md](billing-core.md#taxes-2026-10-18)).

## Service address (design approved 2026-11-05)

On the **billing account**, not the customer: a customer may have lines at several
addresses. It is the TMF666 `contact` document, which was already stored as sent: a contact
with `contactType: "serviceAddress"` and a `GeographicAddressContactMedium` (`street1`,
`street2`, `city`, `stateOrProvince`, `postCode`, `country`). On POST and PATCH of the
account it is validated (422 with the path): US only (`country` US), a US state or
territory code, a ZIP `12345` or ZIP+4 `12345-6789`, `street1` and `city` non-empty. The
normalized address is mirrored in `billing_account.service_address` (jsonb, migration
`0053`), which the tax engine and the order check read; removing the contact (or sending
another contact type) clears it. No new endpoint.

**A prepaid line needs one.** An order of add items on a prepaid account without it is
rejected with the TMF622 error `serviceAddressRequired` (the order's
`productOrderErrorMessage`, as the other prepaid acceptance rules). The check is
`BSS_TAX_REQUIRE_SERVICE_ADDRESS` (default true; the test suite turns it off except in
`tests/test_tax_engine.py`, so the older prepaid tests need no address).

**Existing accounts** keep the old behaviour until an address is set: no address, no
engine, the flat tax of the price, for new purchases only when the check is off and for
renewals of lines bought before. The list of such accounts is built (#207):
`GET /internal/v1/subscriber?serviceAddress=missing` (and the MCP tool `search_subscribers`)
returns the prepaid accounts with a live line (not terminated, cancelled or aborted) and no
`service_address`; the Subscribers screen's **No service address** chip sets it.

## Interface

`quote(charges, address, date)` -> tax lines per charge (jurisdiction, tax type, rate in
percent, amount) and a quote id; keeps nothing at the engine. `commit(key, quote_id)` and
`void(key, quote_id)` take the idempotency key (uuid5 of the charge's ABR) and must answer a
repeated key with the first answer. `TaxUnavailable` means "try later". Stub: per state a
sales/telecom rate, a 3% federal USF and a fixed prepaid wireless 911 fee per state (only on
a charge above zero); ZIP `00000` (also `00000-xxxx`) always raises `TaxUnavailable`;
`committed` / `voided` record the keys sent.

## Flow

- **Acceptance** (order, mid-period offering change, first-charge preview): `quote` sizes
  the hold; nothing is committed. Engine down: 503, nothing held, nothing stored. The quote
  lines and the quote id are kept in the interval quote (`tax_quote_id`), so the hold is
  exactly what is later charged.
- **Posting** (worker): a renewal or a future-dated purchase is quoted at posting. In the
  same transaction as the ABR, the bill and the funded interval, the quote is `commit`ted
  and a `tax_transaction` row is written (address snapshot, lines). An engine error rolls
  the posting back; the command is retried with the existing backoff. **Nothing is charged
  without its tax.**
- **Void**: a credit of the whole charge (a termination on the grant's first day, a mid-period
  change on it) `void`s the transaction in the credit's transaction. A partial credit
  reduces each engine line in proportion to the net kept (`refund_taxes`, rounded once) and
  leaves the engine transaction committed (known limit: no engine adjustment call).
- **Applied tax lines** are TMF678 `appliedTax`: `taxCategory` = the tax type
  (`sales_tax`, `federal_usf`, `prepaid_wireless_e911`), `taxRate` (0 for a fixed fee),
  `taxAmount` and a `jurisdiction` extension attribute (a line has one when it came from
  the engine). The sum is `tax_amount`; bills sum lines as before. A later address change
  never alters a booked charge.
- A plain **balance top-up is not taxed**. Account tax exemptions are not applied to engine
  lines (the engine owns them in a real integration). Add-ons, one-time fees and rated usage
  use the same helper (`app/billing/engine_tax.py`) when their slices land.

## Schema (migration 0053)

`billing_account.service_address` jsonb null; `tax_transaction` (one per taxed ABR:
`applied_rate_id` unique, `quote_id`, `state` committed|voided, `address`, `lines`,
`committed_at`, `voided_at`). Existing rows are untouched.
