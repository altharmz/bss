# Payment gateway: saved cards, card top-ups, auto top-up (issue #185, epic #182)

A subscriber keeps a card on file and tops up the prepaid balance by card, once or
automatically, through a gateway adapter. The gateway is a placeholder today:
`BSS_PAYMENT_ADAPTER=stub` (default; an unknown value fails startup, `app/config.py`). The
interface and the stub are in `app/integrations/payment/`; a real gateway (Stripe or
similar) is a later issue (#203) and keeps this contract. Its keys and a public webhook URL
are created by the human. The design was approved on the issue; the UI is issue 185b.

## No card number, ever (PCI)

The BSS holds only a **gateway token**, brand, last four digits and expiry. Tokens come from
the gateway's client-side form (a future self-care app); the admin UI and tests use stub
tokens. A card number or security code is a 400: `cardNumber`, `number`, `pan`, `cvv`,
`cvc`, `cvv2`, `securityCode` and `nameOnCard` are refused by name, and so is a token that
looks like a card number (13 to 23 digits, spaces or dashes). The token is stored in
`payment_method.gateway_token` and never returned by any API.

## Saved cards: TMF670 (v4, vendored)

`/tmf-api/paymentMethod/v4/paymentMethod` (list, get, create, patch, delete). TMF670 has no
v5 release; v4.0.0 is vendored from `tmforum-apis/TMF670_PaymentMethods`. Only
`tokenizedCard` exists (another `@type` is 422). Attributes: `name`, `description`,
`isPreferred`, `account[]` (exactly one billing account), `brand`, `cardType`,
`lastFourDigits`, `expirationDate` (past: 422), `token` (create only); `status` and
`statusDate` are read-only (400). `tokenType`, `issuer`, `validFor`, `relatedParty`,
`relatedPlace` and `authorizationCode` are not implemented (400, like any unknown
attribute: the spec's other payment method types are out of scope). The same token twice on
an account is 409.

- **Default card** = `isPreferred`; the first card of an account is the default, a later one
  only when it says so; making one preferred unmakes the other (one default per account, a
  partial unique index). Deleting the default promotes none.
- **PATCH** changes `name`, `description` and `isPreferred` only; a card's token, brand,
  digits and expiry never change (delete and save the new card).
- **DELETE** is a soft delete (`status removed`): charges already made keep their reference.
  The card is no longer listed or usable; 409 while a charge with it is pending or sent.
- TMF670 has a hub (`POST /hub`, `DELETE /hub/{id}`, v4 EventSubscription) and emits
  `PaymentMethodCreateEvent`, `PaymentMethodAttributeValueChangeEvent` and
  `PaymentMethodDeleteEvent` like every other API (the spec's listener events are all
  subscribable).

## Gateway commands

`payment_command` (migration 0054): one per card top-up or automatic top-up; its `id` is the
idempotency key sent to the gateway, so every resend names the same charge. States:
`pending` (to send, or retried after a temporary error), `sent` (the gateway has it, its
result is awaited), `confirmed`, `declined` (final), `exhausted`, `cancelled` (reserved).
Kind `refund` exists in the table and the adapter has `refund(key, gateway_ref, amount)`,
but nothing creates or sends a refund command in this slice (adapter only).

The adapter contract: `charge(key, token, amount, currency)` only submits (raises
`PaymentDeclined` for an immediate refusal, `PaymentTemporary` when the answer is unknown);
`result(key)` returns `succeeded`, `declined` (with a reason) or `None` while undecided. A
real gateway also pushes the result to a webhook that records the same result.

The worker (`app/worker/payments.py`, called in every prepaid dispatch right after the
network commands) does, per wake-up: start automatic top-ups; submit the due `pending`
charges (-> `sent`); read the result of the charges that were `sent` **before this wake-up**
(the stub therefore confirms on the next one). Temporary errors retry with backoff (1, 5,
15, 60 minutes) and the command is `exhausted` after `payment_max_attempts` (5); the
internal `POST /internal/v1/payment/command/{id}/retry` (409 in any other state) returns it to
`pending` with the same key and request. The request (token, amount, currency) is built when
the command is made and resent unchanged.

## Card top-up

`POST /internal/v1/billingAccount/{id}/cardTopup {amount: Money, paymentMethod?: {id},
idempotencyKey}` (prepaid accounts only, 409 otherwise; no default card, expired card or
closed account: 409; a card of another account or one that does not exist: 422). The API
**never charges and never changes the balance**: it stores the command (202). The balance
rises only when the gateway confirms: in one transaction under the account lock the worker
records a TMF676 payment (`paymentMethod` name "visa card ending 4242") and the TMF654
top-up (`balance_action`, completed, `paymentMethod` reference to the TMF670 card,
`isAutoTopup` true for an automatic one) and marks the command `confirmed` with their ids.
A **decline** records the gateway's reason on the command (`declineReason`, shown by the
internal API and MCP) and changes nothing: no payment, no top-up. The same
`idempotencyKey` on the same account is the same command (200 with the first one; 409 when
the amount or card differ), so a repeated request never charges twice; the key's uniqueness
is a database index.

## Auto top-up

Opt-in per account, `PATCH /internal/v1/billingAccount/{id}/autoTopup {enabled, threshold,
amount}` (design said PUT; PATCH keeps the MCP generator's POST/PATCH rule and sends the same
partial document: turning it off keeps the values). Prepaid accounts only; turning it on
needs both, `threshold` > 0 and `amount` at least the threshold (422), so one top-up always
ends the shortfall and cannot loop. Columns on `billing_account`: `auto_topup_enabled`,
`auto_topup_threshold`, `auto_topup_amount`, `auto_topup_changed_at`.

Each wake-up, for every enabled prepaid account with a usable default card, no automatic
charge in flight and a spendable balance (`funding.load`) below the threshold, the worker
makes one `auto_topup` charge of the set amount. At most one is in flight: a partial unique
index on `payment_command (billing_account_id) WHERE purpose = 'auto_topup' AND state IN
('pending', 'sent')`. A **declined or exhausted** automatic charge is not repeated until the
balance or the settings change (a payment received after it, or a change of the rule, lets
the next wake-up try again). No default card: nothing is made.

## Not in this slice

The admin UI (payment methods on the subscriber card, "Top up by card", auto top-up
settings: issue 185b), refunds to the card, a gateway webhook endpoint, and a real gateway
(#203). Decimal and the injected clock apply as everywhere: amounts are `numeric(12,2)`,
only `app/clock.py` reads the time.
