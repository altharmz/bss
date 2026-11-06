## Behaviour and deviations

- A `communicationMessage` is one customer SMS or email, queued or sent (docs/decisions/notifications.md).
  The system writes them for prepaid events (low balance, renewal, grace, suspension,
  deactivation, usage 80% and 100%, card declined, port-in status, SIM swap); POST adds a message
  an operator writes. `messageType` is `SMS` or `Email`; `state` is `initial` (queued, or waiting
  for the SMS quiet hours to end), `inProgress` (a send failed, a retry is scheduled),
  `completed`, `cancelled` (`stateReason` noConsent, noRecipient or operator) or `failed`.
  `characteristic[]` carries `category`, `event`, `template`, `stateReason`, `lastError`,
  `providerReference`, `resendOf`, `billingAccount` and `product`.
- `receiver[0]` is exactly one receiver: `email` or `phoneNumber` (E.164 digits), `name` and
  `party` (the Customer; POST completes a missing address from its contact media). `sender` is
  stored as sent; the adapter uses the configured sender.
- POST: `content`, `receiver`, `sender` and `messageType` are required, an Email needs `subject`.
  `characteristic` accepts only `category` (`transactional`, the default, or `marketing`: a
  marketing message needs the customer's recorded opt-in, else it is cancelled `noConsent`).
  `state`, `sendTime`, `sendTimeComplete` and `tryTimes` are read-only (400); `attachment` and
  `logFlag: false` are 422.
- PATCH only cancels an unsent message (`state: cancelled`; 409 once it is completed, failed or
  cancelled). DELETE is 405 (a message is a record) and the spec's `/hub` is not implemented (it
  declares no listener events).
- Lists filter by `id`, `state`, `messageType`, `receiver.party.id`, `billingAccount.id` and
  `product.id`, newest first.
