## Behaviour and deviations

- A `productUsage` is one usage record the network reported for a line (docs/decisions/usage.md).
  `usageType` and `usageSpecification.id` are `data`, `voice` or `sms` (no TMF767 catalog of
  usage specifications). `usageCharacteristic[]` carries `quantity` (data in bytes, voice in
  seconds, sms in messages), `unit`, `eventTime`, `roaming`, `throttled`, the `msisdn`/`imsi`
  that were reported and, for a rejected record, `rejectionReason`. `externalIdentifier[]` holds
  the network's record id (`owner` network, type `networkRecordId`): each id is stored once.
- `product` is the product that held the number on the event date (number assignment history, so
  a record from before a number change maps to the right product). A record no line held is
  stored with status `rejected` (`noLine`) and no product; a matched one is `notRated` until a
  pay-as-you-go line's day closes and it is `rated` (a plan line's stay `notRated`: included usage).
  A rated record answers `usageProductPrice` (the usage price applied, one block, without tax). `creationDate` and `lastUpdate` are when the BSS stored it.
- Lists filter by `id`, `status`, `usageType` and `product.id`, newest event first.
- POST records one usage on a named `product` (`usageSpecification`, `product` and exactly one
  `externalIdentifier` are required, `quantity` too): idempotent, a repeated external id answers
  200 with the stored usage (409 when it names another product or kind). It is checked against
  the plan allowance like a feed record. `serviceUsage`, `relatedParty` and `usageProductPrice`
  are not accepted on POST (422; the price is set by rating).
- PATCH and DELETE are 405: a usage is a fact the network reported. `exportJob` and `/hub` are
  not implemented: the spec's only events are the export job's, and ProductUsage has none.
  The 80% and 100% allowance marks are internal (`usage_threshold`), not TMF events.
