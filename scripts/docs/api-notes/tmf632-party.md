## Behaviour and deviations

- Individuals and organizations share the `party` table; `@type` comes from
  `party_type`. An individual is not reachable under `/organization` and vice versa (404)
  - `app/api/party.py` (`_get`); `tests/test_api_parties.py::test_party_types_are_separate_resources`.
- Individual: `givenName` and `familyName` are both mandatory (as in the spec's create
  schema); organization: `name`. No other TMF632 attributes are stored - an unknown
  attribute is a 400 like everywhere else (`::test_individual_validation`).
