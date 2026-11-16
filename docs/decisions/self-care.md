# Self-care: customer logins and sign-in (issue #195, epic #182)

Index: [README.md](README.md). Related: [users.md](users.md) (operator sign-in, the throttle,
the session check without locks), [accounts.md](accounts.md) (the account PIN lockout, the
audit log), [notifications.md](notifications.md) (the sender).

Slice 1 of customer self-care: identity and sign-in, backend only. The self-care API (#376),
online signup (#377) and the web app (#378) build on it. Design decided on 2026-11-15 under
the human's delegation when the issue was prepared (data-model plan in the issue); this file
records it as built. Migration `0081`.

## Separate from operators

- Customers are never `app_user` rows. A customer session never reaches `/internal/v1` or the
  TMF APIs, and an operator session or service credential is never a customer session.
- The API is ours, under **`/self-care/v1`**: TMF has no customer self-service API. Contract:
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
- Online signup (#377) will create logins itself.

## The scope rule

`app/care/deps.py` `CareActor` is the one place a self-care request learns whose data it may
reach: the session's `customer_id`. Every self-care repository function takes it
(`app/repository/care_logins.py own_customer`). Another customer's object answers **404,
never 403**, so ids are not confirmed. `GET /self-care/v1/customer/{id}` is the first such
read and the proof; the self-care API (#376) adds the rest the same way.

## Alerts

The security-alert detector (accounts.md) tells the customer about every `done` audit entry.
Self-care entries are excluded (`SELF_CARE_ACTIONS`): the customer started them, and the
code went to the login's email. A "your password was changed" alert can come with #376.

## Not in this slice

The other self-care reads and writes (#376), signup (#377), the web app and the operator
screen (#378), a housekeeping rule for old codes and revoked sessions (expired sessions go at the
next sign-in; codes are small rows), audit rows for an operator's invite and disable.

## Tests

`tests/test_self_care.py`: invite, set password, sign in with the code (cookie, hashed token,
masked message log, audit); every refusal (wrong password, unknown and not activated logins alike;
wrong, expired, used, spent and superseded codes; locked; disabled; feature off on every route);
the scope rule (another customer, a missing id); a customer session on `/internal` and the TMF
APIs, an operator session and the service credential on `/self-care`; CSRF; idle and absolute
expiry under the injected clock; sign-out; reset and re-invite; the password policy; invite
refusals; a refused send; throttle and lockout counted; no security alert. MCP tools in
`tests/test_mcp_tools.py`.
