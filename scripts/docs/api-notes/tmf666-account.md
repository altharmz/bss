## Behaviour and deviations

Financial accounts aggregate linked billing-account balances per currency and expose
all linked billing, party and settlement accounts through read-only
`accountRelationship[]`. Their owner is the resolved `relatedParty` with role `owner`;
other roles are stored as sent. New links require the same owner and a financial
account that is not Closed (an unchanged link may be repeated). A financial account's
owner changes only while no accounts link to it (409 otherwise).

Party and settlement accounts are records without a ledger: `accountBalance` is `[]`.
They can link to a financial account with the same owner; unlink or change that link
in the same PATCH when changing to a different owner. Each resource has its own list
and type; partyAccount does not include billing or settlement accounts.

Account documents (contact, taxExemption, paymentPlan, defaultPaymentMethod,
paymentStatus and writable accountRelationship, where defined by the spec) are
validated against the vendored schemas, replaced as a whole by merge patch and
removed by null. Financial account relationships remain derived. paymentStatus is a
client-maintained record; ratingType is postpaid (default) or prepaid (a prepaid account gets its TMF654 bucket at creation, takes no positive creditLimit, and changes mode only before history).
Billing uses billing-account taxExemption certificates by taxType; other account
documents are informational. See [account attributes](../decisions/accounts.md#account-attributes-2026-10-18).
Tests: `tests/test_api_account_attributes.py` covers document round trips, defaults,
owner/customer changes, related-party roles, postpaid rating and cycle offsets.

Bill formats and presentation media expose list/retrieve/create/PATCH. Formats are
JSON detailed (charges and totals) or JSON summary (totals). Names are unique.
Account billStructure.format and presentationMedia reference configuration records;
partial merge-patch preserves unspecified preferences. Format null restores the
default; media []/null clears media. cycleSpecification names `monthly` (null) or a
stored billingCycleSpecification - named instances of the same monthly policy
(billingPeriod/frequency monthly, billingDateShift 0; anything else 422); the built-in
one is fixed, a stored one goes only while no account names it
(`tests/test_api_cycle_specification.py`).
Issued documents snapshot preferences; external delivery is not performed.
The balance formula below is extended by subtracting completed cash refunds.
See [billing completion](../decisions/billing-completion.md).

| Rule | Code | Proven by |
|---|---|---|
| Responses are the extension `BssBillingAccount` (above): `billCycleDay` (1..28) and `currency` (ISO 4217) are optional on create. The day defaults to the creation day in BILLING_TZ (29..31 become 28); currency defaults to creditLimit.unit, else UI_CURRENCY. TMF666 has no currency attribute, and `billStructure.cycleSpecification.billingDateShift` is an offset of the bill date, not the day a period starts - hence the extension | `app/api/account.py` | `tests/test_api_parties.py::test_billing_account_with_extension_and_balance`, `::test_the_extension_schema_is_served_and_valid`, `::test_the_client_checks_the_extension_schema` |
| `currency` is read-only (PATCH 400); `billCycleDay` is fixed for now (PATCH 409 with the reason - how a change treats the current period is an open question) | `patch_account` | `tests/test_api_parties.py::test_billing_account_patch` |
| `relatedParty[]` accepts parties in any role, with exactly one resolved customer anchor (role `customer`, existing customer; 422 otherwise). Other entries are stored as sent and checked against the spec. The customer changes only while the account has no history (409 otherwise), and must retain the linked financial account's owner (422 otherwise) | `AccountCreate`, `CUSTOMER_ROLE`, `create_account`, `account_body` | `::test_billing_account_needs_one_existing_customer`, `::test_billing_account_party_role_must_be_customer`, `::test_billing_account_answers_the_role_it_was_given` |
| `state` is ours: `Active` (default), `Suspended`, `Closed`. Ordering refuses a non-Active account; billing does not look at it | `AccountState` | `tests/test_api_ordering.py::test_account_must_be_active_and_start_not_in_the_past` |
| **Balance** is `accountBalance[]` with exactly one read-only entry `{balanceType: currentBalance, amount}`, computed on every read as payments (`done`) minus all ABRs (tax included) minus completed cash refunds. Positive = the customer is in credit. `accountBalance` in a POST or PATCH is a 400 | `account_body`, `app/repository/billing.py` (`account_balance`) | `tests/test_api_parties.py::test_balance_is_payments_minus_charges_positive_means_credit`, `tests/test_api_inventory_bill.py::test_balance_includes_charges` |
| `creditLimit` (TMF666's own Money): in the account's currency (another unit is a 422), never negative (422), optional, PATCH `null` removes it. **Informational only**: nothing, ordering included, is ever refused because of it | `_credit_limit` | `tests/test_api_credit_limit_and_code.py::test_credit_limit_is_stored_returned_changed_and_removed`, `::test_invalid_credit_limits`, `::test_the_credit_limit_never_blocks_an_order` |
| `opened_at` (not exposed) is set from the injected clock at creation: it decides which billing runs include the account | `create_account` | `tests/test_api_parties.py::test_billing_account_opened_at_comes_from_the_injected_clock` |
| PATCH accepts `@type` `BssBillingAccount` or `BillingAccount` | `check_type` | `tests/test_api_parties.py::test_billing_account_patch` |
| DELETE (204) only what nothing references, else 409 naming the references: a billing account without history (no order - rejected ones too - product, ABR, bill, on-demand request, payment, refund, allocation or billing run outcome; close it instead), its presentation preferences go with it; a financial account no account links to; any party or settlement account; a format or medium no account prefers (issued bills keep their snapshot) | `ACCOUNT_HISTORY`, `delete_*` in `account.py`, `accounts.py`, `presentation.py` | `tests/test_api_deletes.py::test_a_billing_account_with_history_stays`, `::test_an_account_a_billing_run_processed_stays`, `::test_a_financial_account_stays_while_accounts_link_to_it`, `::test_formats_and_media_stay_while_an_account_prefers_them` |
| A delete never races a writer: orders and on-demand requests take the account FOR KEY SHARE, payments, refunds and PATCH FOR UPDATE; the worker skips an account deleted after its run started | `get_billing_account(lock=...)`, `DateRun.process` | `::test_an_account_delete_waits_for_an_order_in_flight`, `::test_a_payment_waits_for_an_account_delete_in_flight`, `::test_the_worker_skips_an_account_deleted_after_its_run_started` |
