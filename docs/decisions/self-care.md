# Self-care: customer logins and sign-in (issue #195, epic #182)

Index: [README.md](README.md). Related: [users.md](users.md) (operator sign-in, the throttle,
the session check without locks), [accounts.md](accounts.md) (the account PIN lockout, the
audit log), [notifications.md](notifications.md) (the sender).

Slice 1 of customer self-care: identity and sign-in, backend only. The self-care API (#376),
online signup (#377) and the web app (#378) build on it. Design decided on 2026-11-15 under
the human's delegation when the issue was prepared (data-model plan in the issue); this file
records it as built. Migration `0081`.

## Moving out of the BSS (2026-11-28, issue #428)

**The human decided that customer self-care becomes a separate application**, in its own
repository `altharmz/selfcare` (`~/projects/selfcare`). The BSS stays a TMF BSS. The app owns
customer identity, signup and the customer web UI, and reaches the BSS only through its TMF
APIs, never `/internal/v1`. It serves TMF910 (Self Care Component Suite v1.0.0, vendored
there) plus its own API to its UI. Its brief, plan and decisions are in that repository.

- What follows describes the self-care still built into the BSS. It stays as built (off by
  default) until the app works end to end, and then #525 removes it. Add no new self-care
  features here.
- #428's first proposal, serving TMF910 from the BSS under a customer session, is dropped.
- BSS work the app needs: a restricted principal (#521); card top-up, auto top-up,
  first-charge quote and history without `/internal/v1` (#522); card details from a TMF670
  token (#523); a pool number assigned on a TMF622 order (#524); allowance usage (#427).

## Separate from operators

- Customers are never `app_user` rows. A customer session never reaches `/internal/v1` or the
  TMF APIs, and an operator session or service credential is never a customer session.
- The API is ours, under **`/self-care/v1`**. It was designed before TMF910 (Self Care Component
  Suite) was found; the self-care app now serves TMF910 (see above). Contract:
  `spec/self-care/bss-self-care.oas.yaml`; tests validate every response against it.
- `app/auth.py` (the dependency on every route) hands every `/self-care/` path to
  `app/care/auth.py authenticate` and returns: the operator cookie and the service headers are
  never read there. The operator check reads only `bss_session`, so a customer cookie on
  another path is a 401. The customer cookie is `bss_care_session`, HttpOnly, SameSite Strict,
  Secure outside dev/test, **path `/self-care/`**, so a browser does not send it anywhere else.
- Writes need `X-Bss-Request: 1`, as the admin UI's do (CSRF).
- Self-care routes are never MCP tools (`AUTH_OPERATIONS`).

## Off by default

`BSS_SELF_CARE_ENABLED` (default `false`). Off: every `/self-care/v1` route answers 404 (the
check runs before anything else), and an operator cannot invite (409), so no self-care email
goes out. Seeing or disabling a login still works. Turning it on in production is the
human's deployment decision. The routes are always mounted; the switch is read on each request,
so tests can turn it on.

## Data (migration 0081)

- `customer_login`: one per customer (`customer_id` unique). `email` is stored **lower case**
  with a CHECK and a plain unique index, instead of `citext`: the issue named citext, but no
  migration has used an extension yet, and creating one in production depends on the database
  role. Lower-casing on the way in gives the same uniqueness. `password_hash` is NULL until the
  customer sets one; a password needs `email_verified_at` (CHECK). `disabled_at`,
  `failed_attempts`, `locked_until`, `created_by`, timestamps. Logins are disabled, never
  deleted.
- `customer_session`: `token_hash` (SHA-256 of a random 32-byte token; the primary key, as in
  `app_session`), `customer_login_id`, `created_at`, `last_seen_at`, `expires_at`,
  `revoked_at`. Sign-out, a password change and a disabled login set `revoked_at`; expired rows
  are deleted when a new session starts.
- `customer_login_code`: `purpose` `sign_in` | `verify_email` | `reset_password`, `code_hash`,
  `expires_at`, `used_at`, `attempts`. A new code of a purpose ends the older open ones.
- `account_audit` takes `care_sign_in`, `care_locked`, `care_password_changed` and the
  verifications `password+code` and `code`. Operator `self-care`, role `customer`, `detail`
  names the login.

## Codes

- **Sign-in code:** 6 digits, 10 minutes, 5 attempts (the fifth wrong one spends it), scrypt
  hash. It is the second factor after the password, so the short code is enough.
- **Link codes** (`verify_email` 24 hours, `reset_password` 1 hour): a random
  `token_urlsafe(24)`, not 6 digits. A reset can be asked for any email without a password,
  so a 6-digit code with 5 attempts per request could be guessed by asking again and again.
  Stored as SHA-256 (high entropy) and looked up by it (`POST /self-care/v1/password` takes
  only the code and the new password).
- **Sending:** the request sends the email itself through the notification sender
  (`app/integrations/notify`), not the worker, and then writes the message to the log
  (`notification_message`, `completed` or `failed`, event types `care_sign_in_code`,
  `care_verify_email`, `care_reset_password`) **with the code masked**. Queuing it for the
  worker would put a live code in TMF681, where every operator could read it and sign in as
  the customer, and the code would wait for the next wake-up. Email has no quiet hours. A
  refused send is a 503 `codeNotSent` (sign-in, invite); a reset answers 204 whatever happens.

## Sign-in

1. `POST /self-care/v1/session` `{email, password}`: throttle, then the password. Right: a sign-in
   code is emailed, 202 `{codeExpiresAt}`. Wrong, unknown, disabled or never activated: the same
   401 `invalidCredentials` after the same scrypt.
2. `POST /self-care/v1/session/verify` `{email, code}`: 200 with the session view and the
   cookie; 401 `codeInvalid` for a wrong, used, superseded or expired code.

- **Throttle:** the operator one (`limit_login`, 20 a minute per address and per name and address,
  in `sign_in_attempt`), with names `care:<email>` so they never mix with operator names. The
  password-set endpoint counts under `care:link-code` per address.
- **Lockout:** 10 wrong passwords in a row lock the login for 15 minutes, the account PIN's
  mechanics (the count restarts, `locked_until` set). While locked, even the right password is
  refused (403 `loginLocked`), not checked and not counted. A right password resets the count.
  The lock writes `care_locked`.
- **Session:** 30 minutes idle (`BSS_CARE_SESSION_IDLE_SECONDS`) and 12 hours absolute
  (`BSS_CARE_SESSION_ABSOLUTE_SECONDS`). The check is the #287 pattern of users.md: a plain
  SELECT without row locks, `last_seen_at` written at most once a minute, the idle limit
  allowed 60 s more. `DELETE /self-care/v1/session` signs out (204).

## Creating a login

- An operator invites: `POST /internal/v1/customer/{id}/careLogin` `{email}` creates the login
  (or starts a disabled or never-activated one again: new email, no password, codes ended)
  and emails a `verify_email` link code. 409 for an active login (disable it first), an email
  another login has, or self-care off.
- The customer sets the password: `POST /self-care/v1/password` `{code, newPassword}`; the
  same endpoint takes a `reset_password` code. The operator password policy applies (12–1024
  characters, not the email). It verifies the email, ends the lockout, every open code and
  every session (sign in again), and writes `care_password_changed` (verification `code`).
- `POST /self-care/v1/password/reset` `{email}`: 204 for any email. An enabled login with a
  password gets a reset code, one without a password a new invitation code (an expired
  invitation needs no operator).
- `GET` / `DELETE /internal/v1/customer/{id}/careLogin`: the login (status `invited`,
  `active`, `locked`, `disabled`; live sessions) and disabling it, which ends its sessions and
  codes at once. GET was added for the operator screen of #378. All three are MCP tools; UI
  parity is #378's operator part.
- Online signup (#377) creates logins itself, with the email verified by its code.

## The scope rule

`app/care/deps.py` `CareActor` is the one place a self-care request learns whose data it may
reach: the session's `customer_id`. Every self-care repository function takes it
(`app/repository/care_logins.py own_customer`). Another customer's object answers **404,
never 403**, so ids are not confirmed. `GET /self-care/v1/customer/{id}` is the first such
read and the proof; the self-care API (#376) adds the rest the same way
(`app/repository/self_care.py`).

## The self-care API (#376)

Slice 2: what a signed-in customer reads and does, backend only (`app/care/api.py`, contract
in the same spec file). Decided on the issue on 2026-11-15; built as below. It adds no money,
ordering or network logic: the reads are the internal API's own views and the writes call the
operator's services, refactored out of their routes for it (`app/internal/accounts.py quote`
and `transaction_body`, `app/internal/usage.py summary`, `app/internal/payments.py
queue_card_topup` and `set_auto_topup`, `app/api/payment_method.py save_card` and
`remove_card`). Responses are our own JSON, never TMF bodies or internal hrefs.

**Reads.** `GET /account` (every account of the customer; `balance` {spendable = ledger less
holds, held} and `autoTopUp` on a prepaid one, null on a postpaid one; `serviceAddress`).
`GET /line` (the prepaid products that are not ended and are not packs: number, plan, kind,
status and paid-through date of the entitlement view, `renewalMode` = the internal
`fundingSource`, the packs live today). `GET /line/{id}/usage` (the internal usage summary
without the barring command's details). `GET /history` (the internal transaction ledger,
paged, without hrefs). `GET /offering?kind=addOn|plan` (sellable, Active and valid offerings
of that kind, each with the operator's firstCharge quote for today: amount, period, available,
shortfall; one without a price in the account's currency is left out). Added beyond the
issue's list because the web app needs them: `GET /paymentMethod` (the saved cards) and
`GET /topUp/{id}` (the gateway's answer to a top-up: a card top-up only queues a command).

**Which account.** A write or read that needs one takes `billingAccountId`; left out, it is
the customer's only prepaid account, and a 422 when there are several or none. A top-up's
account is its card's; an add-on's is its line's.

**Writes**, each audited in `account_audit` with operator `customer:<login id>`, role
`customer`, verification `session` (migration `0087`) and the login in `detail`:
- `POST /topUp {amount, paymentMethodId}` (`care_top_up`): the operator's card top-up command
  (202); the balance rises when the worker reads the gateway's confirmation.
- `POST /paymentMethod {token, billingAccountId?, name?}` (`care_card_saved`): the token alone.
  The adapter gained `card(token)`: the gateway says the brand, last four and expiry (a real
  gateway looks the token up; the stub knows `tok_visa`, `tok_mastercard`, `tok_decline` and
  `tok_fail` and rejects any other: 422 `cardRejected`). The card fields of TMF670 (`brand`,
  `lastFourDigits`, ...) are unknown attributes here (400), so a customer cannot claim a card;
  card numbers and security codes are refused by name on every write, as on TMF670. Prepaid
  accounts only (409).
- `DELETE /paymentMethod/{id}` (`care_card_removed`), `PUT /autoTopUp` (`care_auto_top_up`;
  the internal PATCH's rules).
- `POST /addOn {lineId, offeringId}` (`care_add_on`): a TMF622 order placed with
  `place_order` (the order's actor is the customer, so no PIN rule applies: an add-on is no
  protected action) and its Create event. Accepted: 201 with the order id and state
  (inProgress until the network confirms `change_profile`; the fee is held, then charged).
  Refused: the rejected order is kept, as on TMF622, the audit entry is `refused` with the
  code as reason, and the answer is a 422 whose `code` is the order's first error code (all of
  them in `message`). An offering that is not an add-on is a 404.

**Idempotency.** The three POSTs take `Idempotency-Key` with the P4b journal, keyed by
`customer:<login id>`, so customers' keys never meet. Only successes are kept: a refused add-on
under a key can be bought later with the same key. The PUT and the DELETE are idempotent by
nature and take no key. A top-up's own `client_key` is a fresh `care-<uuid>`: the journal is
what answers a repeat.

**Rate limit.** 30 writes a minute per session (`limit_rate` in `app/internal/users.py`: the
sign-in throttle's table, key `care-write|<session token hash>`, counted in its own
transaction so a refused write counts). Reads are not counted.

**Alerts.** The write actions join `SELF_CARE_ACTIONS`: the customer did them, so no security
alert. The operator's audit screen labels them.

**Not here.** The stub gateway's test page that hands the web app a token belongs to the web
app (#378), which builds the client-side card form. SIM swap, number change, port-out PIN,
plan change, termination and contact or address changes stay operator-only (decided on the
issue).

**Tests.** `tests/care/test_self_care_api.py`: every read with the session's data; another
customer's line, account, card and top-up ids are 404 on every read and write; a saved card
and a card top-up confirmed by the stub (two wake-ups), its replayed key and a key reused for
another amount (409); a declined card; card numbers, security codes and card fields refused
(400) and an unknown token (422); a card removed; auto top-up set and the line's renewal mode;
the limit of 30 writes a minute; an add-on through the order path (held, `change_profile`,
charged once, listed on the line) with its replay; a refused add-on's code, audit and a later
purchase under the same key.

## Online signup (#377)

Slice 3: a new customer signs up on the web (backend only), from an email to a live prepaid
eSIM line. Decided on the issue on 2026-11-15; built as below. Migration `0088`. Code:
`app/care/signup.py` (the steps), `app/worker/signup.py` (what the worker drives),
`app/internal/care_signups.py` (operators), `app/integrations/identity/` (the adapter).

**Identity check adapter.** A placeholder following epic #182: `verify(key, person, address,
document?)` answers None until the service decides, then `pass`, `fail` with a reason or
`review`; `IdentityTemporary` is retried with the network backoff, the same key each time.
`BSS_IDENTITY_ADAPTER=stub` (an unknown value fails startup), `identity_max_attempts` 5. The stub
records a check on its first call and decides on the next wake-up: family name `Declined`
fails, `Review` stays in review, `Unavailable` fails temporarily until `recover()`, anything
else passes. A real vendor joins #203's list.

**Data.** `care_signup` (the resumable record: email, state, offering, the person, the
normalized service address, the identity check, the card and first top-up, and the ids it
created: party, customer, account, login, order, product; the failure code and reason; when
the activation code was shown) and `identity_check` (the durable outbound command, following
`network_command`: `pending` → `sent` → `passed` | `failed` | `review`, or `exhausted`).
States: `started` → `identityPending` → `identityPassed` | `identityFailed` |
`identityReview` → `paid` → `activating` → `active` | `failed`, plus **`expired`** (not in the
issue's list: a signup 24 hours without progress before payment needs a state of its own). A
declined card keeps `identityPassed`; a top-up in flight keeps it from expiring.

**Flow** (`/self-care/v1/signup`, no session: the client keeps the signup's random id, and every
step after the first needs the email verified):
1. `POST {email, offeringId}`: a plan on sale now (422 `offeringUnavailable` otherwise); a
   6-digit `verify_email` code, 30 minutes, 5 attempts, scrypt-hashed on the signup itself
   (`customer_login_code` needs a login, which does not exist yet). Sent by the request and
   logged masked (`care_signup_code`), as the sign-in codes are; 503 `codeNotSent`.
2. `POST /{id}/verify {code}`: 401 `codeInvalid`; 409 `emailInUse` when a self-care login has
   the email. The check is here and not at step 1, so only the owner of the email learns that
   it has a login.
3. `PUT /{id}/details {givenName, familyName, birthDate, serviceAddress}`: the address is
   validated and normalized as a billing account's (taxes.md); the identity check is inserted
   and the signup is `identityPending`. The same details again answer 200; others are a 409.
4. The worker sends the check and asks again on later wake-ups; the client polls `GET /{id}`.
5. `POST /{id}/payment {token, password}`: the operator password policy, the gateway's
   `card(token)` (422 `cardRejected`), card numbers refused (400). In one transaction: the
   individual (birth date and the email as a contact medium), the customer (Active), the
   prepaid billing account (UI currency, the service address) and its bucket, the login (email
   verified, password set), the saved card (the default), with their Create events; then a
   card top-up command for the plan's firstCharge shortfall (the operator's quote, taxes
   included). A repeat while the charge is in flight answers 202 with the state. After a
   decline the same endpoint takes another card: the parties are found, not made again.
6. The worker: confirmed → `paid` and, in the same pass, the TMF622 order (worker actor, no
   PIN): the plan with `simType: esim` (sim.md picks the profile) and the oldest free **pool**
   number (`origin = 'pool'`, unlocked, no claim from today, out of quarantine, `SKIP LOCKED`),
   only for what the offering's specification requires → `activating`.
7. The order completed (the network confirmed `activate`) → `active`. The worker emails the
   activation code once (`care_esim_ready`, logged masked), and the first `GET /{id}` after it
   returns `esim {iccid, activationCode, qrPayload}` (the LPA string of sim.md is the QR
   payload); later reads answer null.

**Who creates what.** The signup creates the parties, the account, the login and the card; the
order alone creates the product; only the worker moves a signup after payment. Each worker step
locks the signup and re-reads its state, so a second tick or worker never repeats a step.

**Failures.** Identity failed: nothing beyond the signup exists. Review (the service said so,
or the check was exhausted): `POST /internal/v1/careSignup/{id}/decision {decision: approve |
reject, reason?}` (MCP `decide_care_signup`; 409 `signupState` in another state) records the
operator in `decided_by`. Card declined: `payment.declineReason`, pay again. No free pool
number: `failed` with `numberUnavailable` and no order; an order refused (`simUnavailable`, ...)
or failed later: `failed` with its first error code. The top-up stays on the new account as
balance. A signup that expires after a declined card keeps the customer, account and login
it created (no product: the customer can sign in and buy a plan). `GET /internal/v1/careSignup` (filters `state`, `email`; MCP `list_care_signups`) and
`GET /careSignup/{id}` (`get_care_signup`) show every signup; never a code or the activation
code. UI parity of the three tools is #378's operator screen (allowlisted).

**Abuse limits.** 3 signups a day per email and 10 an hour per address (`client_address`, the
sign-in throttle's rule), counted on `care_signup` itself under advisory locks: the throttle's
table keeps a minute only. 429 with Retry-After.

**Not here.** Resending the code (start again, within the limit), document upload (the
interface takes one; the stub ignores it), the web pages (#378) and an audit entry for an
operator's decision (`account_audit` needs a customer, which a signup in review does not have:
`decided_by`/`decided_at` keep it).

**Tests.** `tests/care/test_care_signup.py`: the happy path under the injected clock (masked code,
identity sent then passed, top-up, order with an eSIM and a pool number, activation code once
and by email, sign-in, the operator list); the worker running again creating nothing twice;
code, detail and payment replays and refusals; five wrong codes; `emailInUse`; feature off and
CSRF; identity failed, in review with both decisions, and exhausted; a declined card paid
again; a rejected token; an empty pool and no eSIM; expiry by request and by the worker; both
abuse limits. MCP tools in `tests/mcp/test_mcp_tools.py`.

## Alerts

The security-alert detector (accounts.md) tells the customer about every `done` audit entry.
Self-care entries are excluded (`SELF_CARE_ACTIONS`): the customer started them, and the
code went to the login's email. The self-care writes of #376 are excluded the same way.

## Not in this slice

The web app and the operator
screen (#378), a housekeeping rule for old codes and revoked sessions (expired sessions go at the
next sign-in; codes are small rows), audit rows for an operator's invite and disable.

## Tests

`tests/care/test_self_care.py`: invite, set password, sign in with the code (cookie, hashed token,
masked message log, audit); every refusal (wrong password, unknown and not activated logins alike;
wrong, expired, used, spent and superseded codes; locked; disabled; feature off on every route);
the scope rule (another customer, a missing id); a customer session on `/internal` and the TMF
APIs, an operator session and the service credential on `/self-care`; CSRF; idle and absolute
expiry under the injected clock; sign-out; reset and re-invite; the password policy; invite
refusals; a refused send; throttle and lockout counted; no security alert. MCP tools in
`tests/mcp/test_mcp_tools.py`.
