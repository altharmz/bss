## Behaviour and deviations

- A customer is the role a party plays towards us: `engagedParty` is a `PartyRef` whose
  `@referredType` is the party's type (`Individual` / `Organization`); mandatory on
  create, must exist (422), read-only on PATCH (`app/api/customer.py`;
  `tests/test_api_parties.py::test_customer_round_trip`, `::test_customer_validation`).
- `name` is mandatory on create (TMF629's create schema); stored in `customer.name`
  (migration 0002).
- `status` is ours (a free string in TMF629): `Created` (the default), `Active`,
  `Inactive`. PATCH may set any of them; no transition rule. Any other value is a 422
  that changes nothing (`::test_customer_status_outside_the_enum_is_refused_on_patch`).
