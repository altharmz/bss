# Functionality and test-coverage report (cloud probe)

**Describes commit `292fee5`** (`292fee5c350e47a9b43f3bf65e7a3a41f3d2eeb2`, "Cheaper
healthchecks: bash /dev/tcp probe for the app, find -mmin for the worker", = `master` at
the time of the probe). Produced on 2026-10-09 in a claude.ai/code cloud session, branch
`cloud/env-probe`. Built from the existing tracker - `docs/progress/scope.yaml` (83 items),
`docs/PROGRESS.md`, `scripts/progress/generate.py` - not a parallel scope. Neither
`scope.yaml` nor `docs/PROGRESS.md` / `docs/progress/status.json` was modified.

**Unverified until a local `make verify` passes** (see `docs/cloud.md`): the backend
suite ran here on the host (Python 3.12 venv with the exact pins, host PostgreSQL 16.15),
not in the dev image; the UI suites did not run at all.

## Summary per area

| Area | Items | Implemented | Tested - proven by run | Tested - by reading code only | Not tested |
|---|---|---|---|---|---|
| db | 16 | 16/16 (100%) | 16/16 (100%) | - | 0 |
| api | 54 | 54/54 (100%) | 52/54 (96%) + 1 error path only | - | 1 (`api.tmf629.customer.get`) |
| ui | 13 | 10/13 (76%) | 0/13 (0%) | 10/13 (76%) | 3 (the 3 not implemented) |
| worker | 10 | 10/10 (100%) | 10/10 (100%) | - | 0 |
| **all** | **93** | **90/93 (96%)** | **78/93 (83%)** | 10 | 4 + 1 partial |

Percentages are rounded down, like `generate.py`. "Tested" for db/api/ui is not something
the tracker measures yet (step 3, "n/a" in PROGRESS.md); the columns above are this
probe's own measurement, defined under "Method".

## The most important rows

### Not implemented (3)

| Id | Stage | Title | Notes |
|---|---|---|---|
| `ui.billingRuns.list` | 4 | Billing runs: latest run, history, failed badge in the nav | Stage 4 slice g, next per CLAUDE.md. Backend ready: `GET /internal/v1/billingRun`, `.../{id}`, `.../{id}/accounts` all implemented and tested. |
| `ui.billingRuns.catchUp` | 4 | Catch up to a date | Backend `POST /internal/v1/billingRun/catchUp` implemented and tested (13 tests). |
| `ui.billingRuns.retry` | 4 | Retry a failed run | Backend `POST /internal/v1/billingRun/{id}/retry` implemented and tested (8 tests). |

Note for slice g: `frontend/src/app/App.test.tsx` currently asserts the *absence* of
Billing runs (`'has the environment tag, operator and counts - and no Billing runs (D6)'`,
and `/billing` is expected to be the not-found page). Those assertions must flip with it.

### Implemented but not tested (proven by run: no test exercises it)

| Id | What the run showed | Evidence |
|---|---|---|
| `api.tmf629.customer.get` | **Zero requests** to `GET /tmf-api/customerManagement/v5/customer/{customer_id}` in the whole suite (585 tests). Customers are created (196 tests) and listed (2), never read back by id. | instrumented run; `grep` over `tests/` finds no `customer/{id}` GET either |
| `api.tmf632.organization.get` | **Error path only**: the single request is the 404 of an individual's id under `/organization` (`test_api_parties.py::test_party_types_are_separate_resources`). No test reads an existing organization by id (200). | instrumented run |

### Implemented, tested only by reading code (could not run here)

All 10 implemented UI items: they have vitest component tests and are walked by the
Playwright smoke, but neither suite could run in this environment (see `docs/cloud.md`:
host Node is blocked by the guard, and the containers cannot install npm packages).
Their write requests are additionally replayed against the real API by
`tests/test_ui_requests.py`, which *did* run here (4 tests, passed) - that proves the
request bodies, not the UI.

### Thinly tested (exactly one passing test reaches the success path)

`api.tmf632.individual.get`, `api.tmf632.individual.patch`, `api.tmf632.organization.patch`,
`api.tmf629.customer.patch`, `api.tmf678.customerBill.get`,
`api.internal.accountProducts.list`, `api.internal.billingRun.list`,
`api.internal.billingRun.get`, `api.internal.billingRunAccounts.list`. Not gaps, but one
test each; see the API table for which.

## Method

- **Backend runs** (all passed): `ruff check` + `ruff format --check` on `app tests
  alembic` (what `make lint` sees in the container); `pytest -n auto` four times - plain
  (585 passed), instrumented (585 passed, twice; the second after fixing the instrument),
  and after a fresh `scripts/cloud-setup.sh` (585 passed). Plus `python -m
  tests.progress_collect` (67 capability tests passed) and `scripts/progress/generate.py`
  into a scratch directory: **its PROGRESS.md and status.json are byte-identical to the
  committed ones** - no drift at `292fee5`.
- **Instrument**: a throwaway pytest plugin (session scratchpad, not committed), loaded
  with `-p`. It recorded, per test id: every request that reached a FastAPI route
  (`APIRoute.handle`: method, route template, response status - a 405 on the same path
  does not count for an operation); the row count of every table in the test's throwaway
  database just before `tests/conftest.py` drops it; and `@pytest.mark.capability`
  markers. Every test passed, so every recorded request belongs to a passing test.
- **Definitions** - *db tested*: at least one passing test left rows in the table.
  *api tested*: at least one passing test sent a request to the operation that answered
  2xx; "error path only": only 4xx answers. *worker tested*: the tracker's own rule (a
  test marked with the capability passed, none failed). *ui*: the vitest/Playwright tests
  that exercise the item, found by reading them.
- **What "proven by run" does not prove**: that a test *asserts* on that operation or
  table. A request made only as setup (e.g. creating a specification to order something)
  counts. The counts and examples in the tables show where an item is exercised directly.
- The UI marker list (implemented) came from a regex stand-in for
  `frontend/scripts/ui-markers.mjs` (Node is blocked on the host); it found the same 10
  markers as the committed `status.json`.

## Per-item tables

### db

| Id | Impl. | Tested | How | Tests | Evidence: files (tests), examples |
|---|---|---|---|---|---|
| `db.product_specification` | yes | yes | proven by run (rows written) | 211 | `test_api_ordering.py` (32), `test_internal_api.py` (22), `test_worker_queue.py` (16), +21 more; e.g. `test_product_view`, `test_product_filters` |
| `db.product_offering` | yes | yes | proven by run (rows written) | 188 | `test_api_ordering.py` (32), `test_internal_api.py` (22), `test_worker_queue.py` (16), +20 more; e.g. `test_api_catalog.py::test_offering_filters`, `test_offering_references_its_specification` |
| `db.product_offering_price` | yes | yes | proven by run (rows written) | 170 | `test_api_ordering.py` (32), `test_internal_api.py` (22), `test_worker_queue.py` (16), +19 more; e.g. `test_a_cancelled_product_needs_no_price`, `test_api_catalog.py::test_price_round_trip` |
| `db.party` | yes | yes | proven by run (rows written) | 216 | `test_api_ordering.py` (32), `test_api_parties.py` (26), `test_internal_api.py` (23), +20 more; e.g. `test_party_filters`, `test_party_types_are_separate_resources` |
| `db.customer` | yes | yes | proven by run (rows written) | 211 | `test_api_ordering.py` (32), `test_internal_api.py` (23), `test_api_parties.py` (21), +20 more; e.g. `test_customer_filters`, `test_customer_round_trip` |
| `db.billing_account` | yes | yes | proven by run (rows written) | 195 | `test_api_ordering.py` (32), `test_internal_api.py` (23), `test_api_payment.py` (19), +20 more; e.g. `test_billing_account_patch`, `test_billing_account_filters_and_fields` |
| `db.product_order` | yes | yes | proven by run (rows written) | 129 | `test_api_ordering.py` (17), `test_worker_queue.py` (16), `test_internal_price_change.py` (13), +17 more; e.g. `test_add_creates_an_active_product`, `test_delete_terminates_a_started_product` |
| `db.product_order_item` | yes | yes | proven by run (rows written) | 129 | `test_api_ordering.py` (17), `test_worker_queue.py` (16), `test_internal_price_change.py` (13), +17 more; e.g. `test_terminated_product_points_at_both_order_items`, `test_malformed_delete_items` |
| `db.product_order_error_message` | yes | yes | proven by run (rows written) | 17 | `test_api_ordering.py` (10), `test_order_price_chain.py` (5), `test_full_flow.py` (1), +1 more; e.g. `test_a_rejected_order_creates_no_product[start0]`, `test_modify_and_no_change_are_stored_as_rejected` |
| `db.product` | yes | yes | proven by run (rows written) | 121 | `test_worker_queue.py` (16), `test_api_ordering.py` (13), `test_internal_price_change.py` (13), +17 more; e.g. `test_product_view`, `test_product_filters` |
| `db.applied_customer_billing_rate` | yes | yes | proven by run (rows written) | 64 | `test_worker_queue.py` (16), `test_worker_schedule.py` (9), `test_worker_failures.py` (8), +9 more; e.g. `test_applied_billing_rates`, `test_example_8_catch_up_in_one_call` |
| `db.customer_bill` | yes | yes | proven by run (rows written) | 60 | `test_worker_queue.py` (15), `test_worker_schedule.py` (9), `test_worker_failures.py` (8), +9 more; e.g. `test_customer_bill_and_is_billed`, `test_example_8_catch_up_in_one_call` |
| `db.billing_run` | yes | yes | proven by run (rows written) | 82 | `test_worker_queue.py` (16), `test_api_inventory_bill.py` (12), `test_worker_schedule.py` (9), +11 more; e.g. `test_worker_runs.py::test_no_run_yet_starts_one`, `test_example_8_catch_up_in_one_call` |
| `db.payment` | yes | yes | proven by run (rows written) | 13 | `test_api_payment.py` (7), `test_internal_api.py` (3), `test_api_parties.py` (1), +2 more; e.g. `test_payment_list_filters`, `test_payment_is_recorded_as_done` |
| `db.billing_run_account` | yes | yes | proven by run (rows written) | 52 | `test_worker_queue.py` (16), `test_worker_schedule.py` (9), `test_worker_failures.py` (8), +6 more; e.g. `test_a_runs_accounts_with_their_outcomes`, `test_charges_and_outcome_commit_together` |
| `db.worker_request` | yes | yes | proven by run (rows written) | 13 | `test_worker_queue.py` (10), `test_worker_heartbeat.py` (2), `test_worker_schedule.py` (1); e.g. `test_a_catch_up_request_runs_the_dates_in_order`, `test_retry_queues_a_request_for_a_failed_run_once` |

Schema-level tests that read rather than write (not counted above, all passed here):
`test_migrations.py`, `test_nonsuperuser_migration.py`, `test_constraints.py`,
`test_worker_schema.py`, `test_enum_lock.py`, `test_roundtrip.py`.

### api

"Tests (2xx / 4xx)" = passing tests that got that class of answer from the operation.

| Id | Impl. | Tested | How | Tests (2xx / 4xx) | Evidence: files (tests), examples |
|---|---|---|---|---|---|
| `api.tmf620.productSpecification.list` | yes | yes | proven by run | 6 / 3 | `test_api_conventions.py` (9); e.g. `test_equality_filters`, `test_default_and_maximum_limit` |
| `api.tmf620.productSpecification.get` | yes | yes | proven by run | 6 / 2 | `test_api_conventions.py` (7); e.g. `test_patch_is_a_merge_patch`, `test_fields_selects_top_level_attributes` |
| `api.tmf620.productSpecification.create` | yes | yes | proven by run | 196 / 3 | `test_api_ordering.py` (32), `test_internal_api.py` (22), `test_worker_queue.py` (16), +19 more; e.g. `test_add_creates_an_active_product` (setup everywhere) |
| `api.tmf620.productSpecification.patch` | yes | yes | proven by run | 2 / 3 | `test_api_conventions.py` (4); e.g. `test_patch_is_a_merge_patch`, `test_patch_rejects_read_only_attributes` |
| `api.tmf620.productOffering.list` | yes | yes | proven by run | 5 / 1 | `test_api_catalog.py` (2), `test_seed.py` (2), `test_api_credit_limit_and_code.py` (1); e.g. `test_offering_filters`, `test_a_price_belongs_to_at_most_one_offering` |
| `api.tmf620.productOffering.get` | yes | yes | proven by run | 10 / 0 | `test_api_offering_lifecycle.py` (5), `test_api_catalog.py` (3), `test_internal_price_change.py` (2); e.g. `test_offering_references_its_specification`, `test_every_transition[Active-Retired]` |
| `api.tmf620.productOffering.create` | yes | yes | proven by run | 174 / 13 | `test_api_ordering.py` (32), `test_internal_api.py` (22), `test_worker_queue.py` (16), +19 more; e.g. `test_api_catalog.py`, `test_api_credit_limit_and_code.py` (direct) |
| `api.tmf620.productOffering.patch` | yes | yes | proven by run | 19 / 12 | `test_api_offering_lifecycle.py` (11), `test_order_price_chain.py` (7), `test_api_catalog.py` (6), +5 more; e.g. `test_offering_patch`, `test_the_whole_way_forward` |
| `api.tmf620.productOfferingPrice.list` | yes | yes | proven by run | 3 / 0 | `test_api_catalog.py` (3); e.g. `test_price_round_trip`, `test_price_validation` |
| `api.tmf620.productOfferingPrice.get` | yes | yes | proven by run | 10 / 0 | `test_api_catalog.py` (3), `test_api_conventions.py` (3), `test_internal_price_change.py` (2), +2 more; e.g. `test_price_round_trip`, `test_ending_the_price_of_a_live_product_is_refused` |
| `api.tmf620.productOfferingPrice.create` | yes | yes | proven by run | 169 / 15 | `test_api_ordering.py` (32), `test_internal_api.py` (22), `test_api_conventions.py` (22), +18 more; e.g. `test_price_round_trip`, `test_a_price_cannot_start_in_the_past` |
| `api.tmf620.productOfferingPrice.patch` | yes | yes | proven by run | 15 / 7 | `test_api_price_gap.py` (9), `test_order_price_chain.py` (6), `test_api_catalog.py` (2), +2 more; e.g. `test_ending_the_price_of_a_live_product_is_refused`, `test_a_cancelled_product_needs_no_price` |
| `api.tmf632.individual.list` | yes | yes | proven by run | 2 / 1 | `test_api_parties.py` (2); e.g. `test_party_filters`, `test_party_types_are_separate_resources` |
| `api.tmf632.individual.get` | yes | yes (1 test) | proven by run | 1 / 1 | `test_api_parties.py` (2); e.g. `test_individual_round_trip` |
| `api.tmf632.individual.create` | yes | yes | proven by run | 200 / 1 | `test_api_ordering.py` (32), `test_api_parties.py` (25), `test_internal_api.py` (23), +18 more; e.g. `test_individual_round_trip`, `test_individual_validation` |
| `api.tmf632.individual.patch` | yes | yes (1 test) | proven by run | 1 / 1 | `test_api_parties.py` (2); e.g. `test_individual_round_trip` |
| `api.tmf632.organization.list` | yes | yes | proven by run | 2 / 0 | `test_api_parties.py` (2); e.g. `test_party_filters`, `test_party_types_are_separate_resources` |
| `api.tmf632.organization.get` | yes | **error path only** | proven by run | 0 / 1 | `test_api_parties.py` (1); `test_party_types_are_separate_resources` (the 404) |
| `api.tmf632.organization.create` | yes | yes | proven by run | 5 / 1 | `test_api_parties.py` (4), `test_seed.py` (1); e.g. `test_organization_round_trip`, `test_party_filters` |
| `api.tmf632.organization.patch` | yes | yes (1 test) | proven by run | 1 / 1 | `test_api_parties.py` (2); e.g. `test_organization_round_trip` |
| `api.tmf629.customer.list` | yes | yes | proven by run | 2 / 0 | `test_api_parties.py` (2); e.g. `test_customer_filters`, `test_customer_validation` |
| `api.tmf629.customer.get` | yes | **no** | proven by run (no request at all) | 0 / 0 | - |
| `api.tmf629.customer.create` | yes | yes | proven by run | 196 / 1 | `test_api_ordering.py` (32), `test_internal_api.py` (23), `test_api_parties.py` (21), +18 more; e.g. `test_customer_filters`, `test_customer_round_trip` |
| `api.tmf629.customer.patch` | yes | yes (1 test) | proven by run | 1 / 1 | `test_api_parties.py` (2); e.g. `test_customer_round_trip` |
| `api.tmf666.billingAccount.list` | yes | yes | proven by run | 14 / 0 | `test_api_parties.py` (12), `test_seed.py` (2); e.g. `test_billing_account_filters_and_fields`, `test_billing_account_validation[override0-422]` |
| `api.tmf666.billingAccount.get` | yes | yes | proven by run | 7 / 0 | `test_api_parties.py` (2), `test_api_inventory_bill.py` (1), `test_billing_db.py` (1), +3 more; e.g. `test_billing_account_patch`, `test_examples_1_4_11_and_the_tmf678_view` |
| `api.tmf666.billingAccount.create` | yes | yes | proven by run | 181 / 17 | `test_api_ordering.py` (32), `test_internal_api.py` (23), `test_api_payment.py` (19), +18 more; e.g. `test_billing_account_patch`, `test_billing_account_filters_and_fields` |
| `api.tmf666.billingAccount.patch` | yes | yes | proven by run | 4 / 6 | `test_api_credit_limit_and_code.py` (6), `test_api_parties.py` (1), `test_api_ordering.py` (1), +1 more; e.g. `test_billing_account_patch`, `test_account_must_be_active_and_start_not_in_the_past` |
| `api.tmf666.extensionSchema.get` | yes | yes | proven by run | 183 / 0 | `test_api_ordering.py` (32), `test_internal_api.py` (23), `test_api_payment.py` (19), +19 more; e.g. `test_the_extension_schema_is_served_and_valid`, `test_the_client_checks_the_extension_schema` |
| `api.tmf676.payment.list` | yes | yes | proven by run | 2 / 1 | `test_full_flow.py` (1), `test_api_payment.py` (1); e.g. `test_payment_list_filters`, `test_full_flow` |
| `api.tmf676.payment.get` | yes | yes | proven by run | 2 / 0 | `test_api_payment.py` (1), `test_internal_api.py` (1); e.g. `test_payment_is_recorded_as_done`, `test_transactions_carry_the_running_balance` |
| `api.tmf676.payment.create` | yes | yes | proven by run | 13 / 14 | `test_api_payment.py` (19), `test_internal_api.py` (3), `test_api_parties.py` (1), +2 more; e.g. `test_payment_list_filters`, `test_payment_is_recorded_as_done` |
| `api.tmf622.productOrder.list` | yes | yes | proven by run | 2 / 0 | `test_api_ordering.py` (1), `test_full_flow.py` (1); e.g. `test_order_list_and_filters`, `test_full_flow` |
| `api.tmf622.productOrder.get` | yes | yes | proven by run | 3 / 0 | `test_api_ordering.py` (2), `test_full_flow.py` (1); e.g. `test_add_creates_an_active_product`, `test_modify_and_no_change_are_stored_as_rejected` |
| `api.tmf622.productOrder.create` | yes | yes | proven by run | 116 / 16 | `test_api_ordering.py` (32), `test_worker_queue.py` (16), `test_internal_price_change.py` (13), +16 more; e.g. `test_add_creates_an_active_product`, `test_a_rejected_order_creates_no_product[start0]` |
| `api.tmf637.product.list` | yes | yes | proven by run | 5 / 1 | `test_api_inventory_bill.py` (2), `test_full_flow.py` (1), `test_api_ordering.py` (1), +1 more; e.g. `test_product_view`, `test_product_filters` |
| `api.tmf637.product.get` | yes | yes | proven by run | 88 / 0 | `test_worker_queue.py` (16), `test_api_ordering.py` (9), `test_worker_schedule.py` (9), +14 more; e.g. `test_product_view`, `test_add_creates_an_active_product` |
| `api.tmf678.customerBill.list` | yes | yes | proven by run | 3 / 0 | `test_api_inventory_bill.py` (2), `test_billing_db.py` (1); e.g. `test_customer_bill_and_is_billed`, `test_examples_1_4_11_and_the_tmf678_view` |
| `api.tmf678.customerBill.get` | yes | yes (1 test) | proven by run | 1 / 0 | `test_api_inventory_bill.py` (1); `test_customer_bill_and_is_billed` |
| `api.tmf678.appliedCustomerBillingRate.list` | yes | yes | proven by run | 5 / 1 | `test_api_inventory_bill.py` (3), `test_billing_db.py` (1), `test_full_flow.py` (1); e.g. `test_applied_billing_rates`, `test_examples_1_4_11_and_the_tmf678_view` |
| `api.tmf678.appliedCustomerBillingRate.get` | yes | yes | proven by run | 3 / 0 | `test_internal_api.py` (2), `test_api_inventory_bill.py` (1); e.g. `test_applied_billing_rates`, `test_charges` |
| `api.internal.context.get` | yes | yes | proven by run | 6 / 1 | `test_internal_api.py` (7); e.g. `test_context`, `test_context_business_date_is_the_date_in_billing_tz` |
| `api.internal.firstCharge.get` | yes | yes | proven by run | 9 / 10 | `test_internal_api.py` (18), `test_internal_price_change.py` (1); e.g. `test_first_charge_mid_period_today`, `test_first_charge_rounds_half_even` |
| `api.internal.accountProducts.list` | yes | yes (1 test) | proven by run | 1 / 1 | `test_internal_api.py` (2); `test_open_products_with_next_charge_and_price` |
| `api.internal.accountCharges.list` | yes | yes | proven by run | 2 / 2 | `test_internal_api.py` (3), `test_seed.py` (1); e.g. `test_charges`, `test_seed_builds_the_demo_and_is_idempotent` |
| `api.internal.accountTransactions.list` | yes | yes | proven by run | 3 / 2 | `test_internal_api.py` (3), `test_seed.py` (1); e.g. `test_transactions_carry_the_running_balance`, `test_history_paging` |
| `api.internal.priceChange.create` | yes | yes | proven by run | 3 / 11 | `test_internal_price_change.py` (13), `test_ui_requests.py` (1); e.g. `test_a_live_price_changes_in_one_step`, `test_price_change_as_the_ui_sends_it` |
| `api.internal.billingRun.list` | yes | yes (1 test) | proven by run | 1 / 1 | `test_worker_queue.py` (2); `test_runs_are_listed_newest_first_with_derived_counts` |
| `api.internal.billingRun.get` | yes | yes (1 test) | proven by run | 1 / 1 | `test_worker_queue.py` (2); `test_runs_are_listed_newest_first_with_derived_counts` |
| `api.internal.billingRunAccounts.list` | yes | yes (1 test) | proven by run | 1 / 5 | `test_worker_queue.py` (6); `test_a_runs_accounts_with_their_outcomes` |
| `api.internal.billingRun.catchUp` | yes | yes | proven by run | 9 / 4 | `test_worker_queue.py` (10), `test_worker_heartbeat.py` (1), `test_worker_schedule.py` (1); e.g. `test_a_catch_up_request_runs_the_dates_in_order`, `test_catch_up_is_refused_before_the_run_is_due` |
| `api.internal.billingRun.retry` | yes | yes | proven by run | 5 / 3 | `test_worker_queue.py` (6), `test_worker_heartbeat.py` (1); e.g. `test_retry_queues_a_request_for_a_failed_run_once`, `test_a_retry_request_reruns_a_run_failed_as_a_whole` |
| `api.service.healthz` | yes | yes | proven by run | 2 / 0 (+1 503) | `test_health.py` (2), `test_billing_status.py` (1); e.g. `test_healthz_ok_when_db_reachable` |
| `api.service.billingStatus` | yes | yes | proven by run | 2 / 1 (+4 503) | `test_billing_status.py` (5); e.g. `test_a_completed_run_is_fresh_until_the_next_one_is_overdue`, `test_a_running_run_is_503_once_overdue` |

### ui

Nothing below ran here. "Implemented" = the `data-ui` marker exists (same 10 as the
committed `status.json`); "tested" = tests found by reading `frontend/src/**/*.test.tsx`
and `frontend/e2e/smoke.spec.ts`.

| Id | Impl. | Tested | How | Evidence |
|---|---|---|---|---|
| `ui.shell` | yes | yes | by reading code | `src/app/App.test.tsx` describe `shell` (5): `shows the business date and time zone from /internal/v1/context`, `shows the failing context call and recovers on Retry`, ...; e2e `subscribers, a card with its first-charge preview, the catalog` (business date in the banner) |
| `ui.subscribers.list` | yes | yes | by reading code | `src/screens/Subscribers.test.tsx` (6): `shows one row per billing account with balance, products and cycle day`, `filters by the real account state, with counts`, ...; e2e smoke |
| `ui.subscriber.card` | yes | yes | by reading code | `src/screens/SubscriberCard.test.tsx` describe `subscriber card` (8): `shows the account: state, ids, customer status and the KPIs`, `shows the charge history with periods, proration and signs`, ...; e2e smoke |
| `ui.subscriber.addProduct` | yes | yes | by reading code | `SubscriberCard.test.tsx` describe `add product` (4): `orders the offering with the start date and closes`, ...; e2e smoke (first-charge preview); request bodies proven by run: `tests/test_ui_requests.py::test_add_and_delete_orders_as_the_ui_sends_them` |
| `ui.subscriber.deactivate` | yes | yes | by reading code | `SubscriberCard.test.tsx`: `deactivates through a TMF622 delete order, confirming first`, `shows the reasons of a rejected order in the row`; bodies proven by run: `test_add_and_delete_orders_as_the_ui_sends_them` |
| `ui.catalog.list` | yes | yes | by reading code | `src/screens/Catalog.test.tsx` describe `catalog` (4): `lists offerings with code, specification, price, period, status and validity`, ...; e2e smoke |
| `ui.catalog.offeringDetail` | yes | yes (indirect) | by reading code | no test of its own; the `lifecycle` and `change price` tests in `Catalog.test.tsx` open it by a row click (`screen.getByRole('dialog', { name })`) |
| `ui.catalog.lifecycle` | yes | yes | by reading code | `Catalog.test.tsx` describe `lifecycle` (4): `activates an In Design offering with a merge patch, after confirming`, `retires an Active offering only after the warning`, ...; bodies proven by run: `test_ui_requests.py::test_lifecycle_steps_are_merge_patches` |
| `ui.catalog.changePrice` | yes | yes | by reading code | `Catalog.test.tsx` describe `change price` (5): `sends one atomic change with the exact amount and closes`, ...; bodies proven by run: `test_ui_requests.py::test_price_change_as_the_ui_sends_it` |
| `ui.catalog.newOffering` | yes | yes | by reading code | `Catalog.test.tsx` describe `new offering` (3), `src/screens/newOffering.test.ts` (3); bodies proven by run: `test_ui_requests.py::test_new_offering_is_stored_as_the_ui_means_it` |
| `ui.billingRuns.list` | **no** | no | - | - |
| `ui.billingRuns.catchUp` | **no** | no | - | - |
| `ui.billingRuns.retry` | **no** | no | - | - |

### worker

The tracker's own rule, run here: `tests/progress_collect.py` ran the 67 tests marked
`@pytest.mark.capability` - all passed, none failed, every id seen is a scope item.

| Id | Impl. | Tested | How | Tests | Evidence: files (tests), examples |
|---|---|---|---|---|---|
| `worker.runLifecycle` | yes | yes | proven by run (capability marker) | 4 | `test_worker_runs.py` (4); e.g. `test_no_run_yet_starts_one`, `test_a_failed_run_is_reopened`, `test_a_completed_run_is_a_noop_not_an_error` |
| `worker.takeover` | yes | yes | proven by run (capability marker) | 4 | `test_worker_failures.py` (3), `test_worker_process.py` (1); e.g. `test_no_takeover_while_the_owner_is_alive`, `test_a_takeover_after_a_death_between_accounts`, `test_a_dropped_connection_frees_the_lock_for_a_takeover` |
| `worker.doubleStart` | yes | yes | proven by run (capability marker) | 2 | `test_worker_runs.py` (2); `test_a_second_worker_backs_off_and_writes_nothing`, `test_ownership_is_released_explicitly_too` |
| `worker.failureIsolation` | yes | yes | proven by run (capability marker) | 3 | `test_worker_process.py` (3); `test_a_failed_account_blocks_only_itself_and_catches_up_in_order`, `test_a_catch_up_stops_at_the_next_failure_and_later_dates_wait_for_it`, `test_retrying_a_later_run_leaves_accounts_blocked_on_an_earlier_date` |
| `worker.wholeRunFailure` | yes | yes | proven by run (capability marker) | 9 | `test_worker_failures.py` (9); e.g. `test_a_lock_timeout_is_a_whole_run_failure`, `test_a_second_failure_waits_longer`, `test_the_backoff_is_5_15_then_hourly[1-5]` |
| `worker.completedAllOk` | yes | yes | proven by run (capability marker) | 6 | `test_worker_process.py` (6); e.g. `test_completed_when_every_member_is_ok`, `test_not_completed_while_a_member_is_not_ok[failed]` |
| `worker.requestQueue` | yes | yes | proven by run (capability marker) | 6 | `test_worker_queue.py` (6); e.g. `test_a_catch_up_request_runs_the_dates_in_order`, `test_a_retry_request_retries_only_the_failed_accounts` |
| `worker.statusFlips` | yes | yes | proven by run (capability marker) | 10 | `test_worker_flips.py` (10); e.g. `test_flips_follow_the_dates`, `test_only_orders_completed_before_midnight_count`, `test_other_statuses_are_never_touched[active]` |
| `worker.scheduler` | yes | yes | proven by run (capability marker) | 21 | `test_worker_schedule.py` (20), `test_worker_signals.py` (1); e.g. `test_once_bills_the_due_date_and_exits`, `test_a_wake_up_processes_the_queue`, `test_the_clock_is_read_in_billing_tz` |
| `worker.signals` | yes | yes | proven by run (capability marker) | 2 | `test_worker_signals.py` (2); `test_sigterm_finishes_the_account_in_hand_and_stops`, `test_sigkill_leaves_nothing_half_written_and_the_next_worker_takes_over` |

## Functionality in the code that scope.yaml does not track

The tracker's unscoped check is clean (no unscoped table, operation or marker - it
matches only those three keys). Beyond them:

| What | Where | Tested? |
|---|---|---|
| **Billing core (stage 3)**: proration, half-even rounding, refunds, bills, replay, visibility cut-off - no item in any area | `app/billing/core.py`, `adapter.py`, `app/repository/billing.py` | yes, proven by run: `test_billing_core.py` (32 tests), `test_billing_properties.py` (7, hypothesis `dev` profile = 50 examples), `test_billing_db.py` (8) |
| **Order processing rules** (states, rejection codes, row locks) - only reachable as `api.tmf622.productOrder.create` | `app/ordering/` | yes, proven by run: `test_order_states.py` (7), `test_order_concurrency.py` (1), `test_order_price_chain.py`, `test_api_ordering.py` |
| **Worker liveness heartbeat** (`/tmp/worker-alive` after every account; the prod healthcheck depends on it) - no worker item, no capability marker | `app/worker/` (`DateRun(on_account=...)`) | yes, proven by run: `test_worker_heartbeat.py` (4) |
| **Worker start-up refusals** (`BILLING_RUN_AT` window, `WORKER_FAKE_NOW` only with `APP_ENV=test`, `APP_ENV` unset = prod) | `app/worker/__main__`, `app/config.py` | yes, proven by run: `test_worker_runtime.py` (5), parts of `test_worker_process.py` without markers |
| **Demo seed** (`make seed`) | `tests/seed.py` | yes, proven by run: `test_seed.py` (4) |
| FastAPI's default `/docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect` - not in `app.openapi()`, so invisible to the tracker; reachable behind Caddy's basic auth | `app/main.py` (FastAPI defaults) | not tested; CLAUDE.md (now [decisions/tmf-api.md](decisions/tmf-api.md)) treats `/docs` as deliberate (no response schemas) |

## Anything in scope.yaml that looks stale

No item looks stale: every planned item exists in the code except the three
`ui.billingRuns.*` items, which are the next slice (g), and both `excluded` entries still
describe real code (`GET /` exists; `ui.notFound` has no marker, so it is a pre-emptive
exclusion, harmless). Nearby text that is out of date (not scope.yaml, not changed here):

- CLAUDE.md (now [decisions/harness.md](decisions/harness.md)), "Progress tracker": "Tested ... not measured yet; deployed ... Both come
  after slice b" - slice b is long done; still "n/a" / "unknown".
- CLAUDE.md (now [decisions/harness.md](decisions/harness.md)), "Pre-commit hook": "418/418 each" - the suite is 585 tests now.
- `App.test.tsx` asserts there is no Billing runs nav item (D6); correct today, must flip
  in slice g.
