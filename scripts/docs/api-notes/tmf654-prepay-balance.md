## Behaviour and deviations

- A `bucket` is a view of a prepaid account's funding ledger: one `monetary` bucket per
  prepaid billing account and currency, with a stable id. `remainingValue` is the
  spendable amount L - H and `reservedValue` the outstanding holds H, both
  `{amount, units}` in the account currency (exact cents); `remainingValueName` is the
  same amount as text. L is the account's TMF666 `currentBalance`: payments and signed
  adjustments, reversals and completed transfers in/out, less tax-inclusive charges and
  payouts. L - H is not clamped: a debt shows as a negative amount.
- `status` follows the account: `active` (Active), `suspended` (Suspended), `expired`
  (Closed; the bucket stays readable). `partyAccount` is the billing account (with its
  state as `status`), `relatedParty` its customer. `product[]` lists the account's
  products not yet ended today, `logicalResource[]` the number each of them holds (today's
  assignment, else its next accepted claim); `isShared` is true with more than one number.
  `validFor` starts when the bucket was created and has no end: monetary credit does not
  expire. A bucket is never a deduction, so it has no `requestedDate`/`confirmationDate`.
- `accumulatedBalance` totals the account's buckets (one today): `totalBalance` is the
  sum of their `remainingValue`, `bucket[]` references them. Its v4 `logicalResource` is a
  single reference, given only when exactly one number is funded.
- Reads never write: a bucket exists only once the account has one (prepaid account
  creation, P5a). Lists filter by `id`, `partyAccount.id`, `status`, `usageType`
  (bucket) and `id`, `partyAccount.id` (accumulatedBalance), with standard paging and
  `fields`.
- POST/DELETE `/hub` subscribe to the twelve balance-action events (v4
  EventSubscription: no href, no `@type`). Adjustment, reservation and transfer actions
  and `balanceActionHistory` are not implemented yet; their endpoints answer 404
  (`tests/tmf-gaps.txt`).

### topupBalance

- POST records money received on a bucket: `amount` (`{amount, units}`, exact cents in the
  bucket's currency), `usageType: monetary`, `bucket` and `partyAccount` (which must own the
  bucket). It creates the TMF676 payment (`done`, settled like any payment) and the
  completed top-up in one transaction and answers 201 with `status: completed`. Without
  `paymentMethod` the payment is named `Unspecified recorded receipt`: a receipt asserted by
  the operator, never implied to be collected money. `logicalResource[]`/`product[]` must be
  the account's live numbers/products. `Idempotency-Key` replays the stored 201; a different
  body is 409.
- A business refusal (a closed account, a number or product that is not the account's) is
  stored `failed`, answered 201, and sends `TopupBalanceFailureEvent`; no payment exists.
  `reason` stays the client's; a refused top-up without one states why. Suspended accounts
  may be topped up. Malformed or unknown references, a currency mismatch, `usageType` other
  than `monetary`, `isAutoTopup: true`, `recurringPeriod`, `numberOfPeriods` and `voucher`
  are 4xx and store nothing.
- A payment POSTed straight to a prepaid account (with a bucket) records exactly one
  completed top-up, so the top-up list is complete whichever API took the money. It does
  not call the top-up API, and a replayed payment (same `Idempotency-Key`) records no second.
- PATCH requires `status`. `cancelled` reverses a completed top-up only while its payment
  is entirely unspent, unallocated and unheld (409 otherwise) by appending a compensating
  entry; the payment stays `done` and no payout is invented. `reason` and `description` may
  change; everything else may only be repeated (409 otherwise). A failed, cancelled or
  final top-up cannot change status. `TopupBalanceCancelEvent` follows a cancellation.
- DELETE removes only a failed top-up (409 otherwise) and retires its idempotency key.
- Lists filter by `id`, `status`, `bucket.id` and `partyAccount.id`.

Tests: `tests/test_api_balance.py`, `tests/test_api_topup.py`, `tests/test_api_hub.py`,
`tests/test_mcp_tools.py`; the gap ledger locks the absent endpoints and the deviations.
