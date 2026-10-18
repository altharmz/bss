## Behaviour and deviations

- **v4**: TMF676 has no v5; this is the only v4 API here.
- POST records a payment that **has been received**: `status` is always `done`,
  `paymentDate` and `statusDate` are the injected clock at POST. There is no payment
  processing (`app/api/payment.py`; `tests/test_api_payment.py::test_payment_is_recorded_as_done`).
- Mandatory: `account`, `totalAmount`, `paymentMethod` (with a `name` or an `@type`; the
  one word is stored and returned as both) - `::test_payment_method_is_mandatory`.
- `amount` (net) and `taxAmount` split `totalAmount`: either gives the other, both must
  add up (`::test_amount_and_tax_amount_split_the_total`); a `paymentItem` allocates
  `amount + taxAmount` ([taxes](../decisions/billing-core.md#taxes-2026-10-18)).
  `totalAmount.value` must be positive.
- `totalAmount.unit` must be the account's currency (422,
  `::test_currency_must_match_the_account`); the composite foreign key behind it maps to a
  409 if it ever fires (`::test_currency_foreign_key_violation_is_a_409_not_a_500`).
- Payments are never changed or deleted: PATCH/DELETE are 405 (`::test_payments_cannot_be_changed`).
- Refunds expose list/retrieve/create. POST records a completed payout, never external
  payment processing. It consumes unused payments then issued credit-note credit, or
  only a named payment. It cannot exceed positive net balance (including unbilled
  charges). The account lock and combined source bounds prevent double spending.
  The ledger calls cash refunds payout; negative ABR refunds remain account credits.
  Refund method name/type are preserved. Records are immutable. See
  [billing completion](../decisions/billing-completion.md), tested by
  tests/test_billing_completion.py.
