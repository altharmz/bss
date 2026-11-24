## Behaviour and deviations

- Individuals and organizations share the `party` table; `@type` comes from
  `party_type`. An individual is not reachable under `/organization` and vice versa (404)
  - `app/api/party.py` (`_get`); `tests/tmf/test_api_parties.py::test_party_types_are_separate_resources`.
- Individual: `givenName` and `familyName` are both mandatory (as in the spec's create
  schema); organization: `name`. No other TMF632 attributes are stored - an unknown
  attribute is a 400 like everywhere else (`::test_individual_validation`).
- DELETE (204) only a party nothing refers to: a 409 names the customers engaging it
  and the financial, party and settlement accounts it owns. Customers and accounts take
  the party FOR KEY SHARE when they refer to it, so a delete never races them - `_delete`;
  `tests/tmf/test_api_deletes.py::test_a_party_stays_while_a_customer_or_an_account_refers_to_it`,
  `::test_a_new_customer_waits_for_a_party_delete_in_flight`.
