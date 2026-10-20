## Behaviour and deviations

- Subscriber numbers are `LogicalResource` instances with unique canonical E.164
  `value` (8..15 ASCII digits, first nonzero, no plus or formatting), a required display
  name and the immutable built-in `resourceSpecification.id: msisdn`. The specification
  has no catalog URL. Other resource subclasses are outside this release.
- `name`, `description`, descriptive documents and `administrativeState` (`locked` or
  `unlocked`) are editable. Locking preserves current and future accepted assignments.
  Network, alarm and lifecycle states are unsupported; no provisioning is implied.
- `usageState` is `busy` during a current assignment, otherwise `idle`.
  `allocationStatus` is `allocated`, `reserved` for a future claim, `quarantined` after
  release, otherwise `available`, in that priority order. States use the injected
  business date and half-open assignment intervals; cancelled and empty claims occupy
  no dates. `MSISDN_QUARANTINE_DAYS` defaults to zero and must be nonnegative.
- Creation date, usage/allocation and the fixed specification may only repeat their
  server values (422 otherwise). Changing a number value gives 409. Descriptive
  documents are checked recursively against the pinned TMF639 schemas, stored and
  returned as sent, with date-times normalized to UTC. They do not change assignments.
- Lists support equality filters on `id`, `name`, `value`, `administrativeState`,
  `usageState`, `allocationStatus`, `resourceSpecification.id`; counts and paging apply
  after filtering. Standard `fields` projections and all four declared PATCH formats
  are supported. DELETE permits only never-assigned numbers; any retained assignment
  history, including cancelled versions, gives 409.
- POST/GET/DELETE hubs subscribe to the four resource events. Create/delete, lock state
  changes and descriptive changes use the transactional outbox; no-op PATCH emits no
  event. All eight operations are also MCP tools. Order integration follows in N4.

Tests: `tests/test_api_resource_inventory.py`, `tests/test_api_hub.py`,
`tests/test_mcp_tools.py`; the gap ledger locks excluded attributes and subclasses.
