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
  EventSubscription: no href, no `@type`). `balanceActionHistory` is not implemented yet; its
  endpoints answer 404 (`tests/tmf-gaps.txt`).

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

### adjustBalance

- POST corrects a bucket by a signed `amount` (`{amount, units}`, exact nonzero cents in the
  bucket's currency, `usageType: monetary`, `bucket`; no `partyAccount`, the owner follows
  the bucket). Positive credits it with one adjustment source, spendable at once. Negative
  debits it, limited to the spendable funds (ledger less holds and unpaid charges), and
  consumes specific sources in funding order (payments, credit notes, then adjustment,
  transfer and reversal sources). `adjustType` is `oneTime` (`recurring` is a 422).
  `Idempotency-Key` replays the stored 201; a different body is 409.
- A refusal (a closed account, a debit above the spendable funds, a number or product that
  is not the account's) is stored `failed`, answered 201, with `AdjustBalanceFailureEvent`.
  Malformed or unknown references, units other than the bucket's and a zero amount are 4xx
  and store nothing.
- PATCH requires `status`. `cancelled` reverses a completed adjustment by appending a
  compensating entry: a credit's source is consumed, so it is refused (409) once spent,
  allocated or held; a debit restores each source it consumed, once, as a spendable
  reversal source. `reason` and `description` may change; everything else may only be
  repeated (409 otherwise). `AdjustBalanceCancelEvent` follows a cancellation.
- DELETE removes only a failed adjustment (409 otherwise) and retires its idempotency key.
- Lists filter by `id`, `status`, `bucket.id` and `partyAccount.id`.

### reserveBalance

- POST holds an `amount` (`{amount, units}`, exact positive cents in the bucket's currency,
  `usageType: monetary`, `bucket`, `partyAccount` = the bucket's owner) of the spendable
  funds, pinned to specific sources, so no order, renewal, debit, refund or settlement can
  spend it. `validFor.endDateTime` (optional, in the future; a `startDateTime` may not be
  later than now) ends it. The ledger and the bucket's `remainingValue` + `reservedValue`
  stay equal; only the split changes. `Idempotency-Key` replays the stored 201.
- A hold past its end is released, once, before the next spend under the account lock (order,
  renewal, debit, refund, settlement, another hold) and by the worker's wake-up; it becomes
  `cancelled` with `ReserveBalanceCancelEvent`. A GET between the end and that release can
  still read `created`.
- A refusal (a closed account, an amount above the spendable funds, a number or product that
  is not the account's) is stored `failed`, answered 201, with `ReserveBalanceFailureEvent`.
- PATCH requires `status` and `reason`. `cancelled` releases a created hold; a hold an order
  or renewal owns is a 409. `reason` and `description` may change; everything else may only
  be repeated (409 otherwise).
- DELETE removes only a failed hold (409 otherwise) and retires its idempotency key.
- Lists filter by `id`, `status`, `bucket.id` and `partyAccount.id`.

### transferBalance

- POST moves nothing: it validates both sides under the two accounts' row locks (taken in id
  order), holds the sender's `amount` plus an originator-paid `transferCost` (the receiver
  pays it from the incoming amount, so only the `amount` is held then), queues one `transfer`
  command and answers 201 `created`. The billing worker completes it later (not yet built),
  so the sender's `remainingValue` drops and `reservedValue` rises by the hold while the
  ledger stays put. `Idempotency-Key` replays the stored 201.
- Required: `reason`, `channel`, `logicalResource` (sender numbers, all of the sender
  account), `receiverLogicalResource`, `amount`, `usageType`, `bucket`, `receiverBucket` and
  `receiverBucketUsageType` (monetary only). `transferCost` is `{unit, value}` in the same
  currency, absent meaning zero; a receiver-paid one may not exceed `amount` (422).
  `costOwner` defaults to `originator`; both are always returned. `partyAccount` is the
  sender's.
- A refusal is stored `failed`, answered 201, with `TransferBalanceFailureEvent`, and holds
  and queues nothing; `description` states why: `sameBucket`, `currency` (the receiver
  bucket's differs), a number or product that is not its account's, a closed receiver, a
  closed or suspended sender (a suspended receiver still receives), or too few spendable
  funds for the amount and fee. An unknown bucket, number or product is a 4xx and stores
  nothing.
- A transfer is final: the v4 update has no status, so nothing cancels it; a correction is a
  new opposite transfer. PATCH requires `reason`, `channel`, `logicalResource` and
  `receiverLogicalResource`; only `reason` changes, everything else may only be repeated (409
  otherwise). `TransferBalanceCancelEvent` is never sent.
- DELETE removes only a failed transfer (409 otherwise) and retires its idempotency key.
- Lists filter by `id`, `status`, `bucket.id`, `receiverBucket.id` and `partyAccount.id`.

Tests: `tests/test_api_balance.py`, `tests/test_api_topup.py`, `tests/test_api_adjust.py`,
`tests/test_api_reserve.py`, `tests/test_api_transfer.py`, `tests/test_api_hub.py`,
`tests/test_mcp_tools.py`; the gap ledger locks the absent endpoints and the deviations.
