## Behaviour and deviations

- **v4**: TMF676 has no v5; this is the only v4 API here.
- POST records a payment that **has been received**: `status` is always `done`,
  `paymentDate` and `statusDate` are the injected clock at POST. There is no payment
  processing (`app/api/payment.py`; `tests/test_api_payment.py::test_payment_is_recorded_as_done`).
- Mandatory: `account`, `totalAmount`, `paymentMethod` (with a `name` or an `@type`; the
  one word is stored and returned as both) - `::test_payment_method_is_mandatory`.
- Taxes are out of scope: `amount`, if sent, must equal `totalAmount`; `taxAmount`, if
  sent, must be zero (`::test_amount_may_repeat_total_but_taxes_are_out_of_scope`).
  `totalAmount.value` must be positive.
- `totalAmount.unit` must be the account's currency (422,
  `::test_currency_must_match_the_account`); the composite foreign key behind it maps to a
  409 if it ever fires (`::test_currency_foreign_key_violation_is_a_409_not_a_500`).
- Payments are never changed or deleted: PATCH/DELETE are 405 (`::test_payments_cannot_be_changed`).
- Refunds of payments (`/refund`) are not implemented.
