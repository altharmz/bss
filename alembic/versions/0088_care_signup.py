"""Online signup: the resumable signup record and the identity check command (#377).

Revision ID: 0088
Revises: 0087

docs/decisions/self-care.md "Online signup".

- `care_signup`: one signup from the email to an active eSIM line, with the ids it created.
- `identity_check`: the durable outbound identity check of a signup, sent and retried by the
  worker through the identity adapter (app/integrations/identity), following
  `network_command`.
"""

from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None

STATES = (
    "'started', 'identityPending', 'identityPassed', 'identityFailed', 'identityReview',"
    " 'paid', 'activating', 'active', 'failed', 'expired'"
)


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE care_signup (
            id uuid PRIMARY KEY,
            email text NOT NULL CHECK (email <> '' AND email = lower(email)),
            state text NOT NULL DEFAULT 'started' CHECK (state IN ({STATES})),
            product_offering_id uuid NOT NULL REFERENCES product_offering(id),
            client_address text NOT NULL,
            email_code_hash text NOT NULL,
            email_code_expires_at timestamptz NOT NULL,
            email_code_attempts integer NOT NULL DEFAULT 0 CHECK (email_code_attempts >= 0),
            email_verified_at timestamptz,
            given_name text,
            family_name text,
            birth_date date,
            service_address jsonb CHECK (jsonb_typeof(service_address) = 'object'),
            identity_check_id uuid,
            identity_reason text,
            decided_by text,
            decided_at timestamptz,
            payment_method_id uuid REFERENCES payment_method(id),
            payment_command_id uuid REFERENCES payment_command(id),
            payment_error text,
            party_id uuid REFERENCES party(id),
            customer_id uuid REFERENCES customer(id),
            billing_account_id uuid REFERENCES billing_account(id),
            customer_login_id uuid REFERENCES customer_login(id),
            product_order_id uuid REFERENCES product_order(id),
            product_id uuid REFERENCES product(id),
            failure_code text,
            failure_reason text,
            activation_shown_at timestamptz,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CHECK ((state = 'failed') = (failure_code IS NOT NULL)),
            CHECK (state IN ('started', 'expired') OR email_verified_at IS NOT NULL),
            CHECK (state NOT IN ('paid', 'activating', 'active') OR billing_account_id IS NOT NULL),
            CHECK (state NOT IN ('activating', 'active') OR product_order_id IS NOT NULL)
        );
        CREATE INDEX care_signup_email_idx ON care_signup (email, created_at);
        CREATE INDEX care_signup_address_idx ON care_signup (client_address, created_at);
        CREATE INDEX care_signup_open_idx ON care_signup (state, updated_at)
            WHERE state NOT IN ('active', 'failed', 'expired', 'identityFailed');
        CREATE INDEX care_signup_created_idx ON care_signup (created_at DESC, id);

        CREATE TABLE identity_check (
            id uuid PRIMARY KEY,
            care_signup_id uuid NOT NULL REFERENCES care_signup(id),
            state text NOT NULL DEFAULT 'pending' CHECK (state IN
                ('pending', 'sent', 'passed', 'failed', 'review', 'exhausted')),
            request jsonb NOT NULL CHECK (jsonb_typeof(request) = 'object'),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            next_attempt_at timestamptz,
            last_error text,
            reason text,
            created_at timestamptz NOT NULL,
            attempted_at timestamptz,
            decided_at timestamptz,
            CHECK ((state IN ('passed', 'failed', 'review')) = (decided_at IS NOT NULL))
        );
        CREATE INDEX identity_check_signup_idx ON identity_check (care_signup_id);
        CREATE INDEX identity_check_open_idx ON identity_check (created_at, id)
            WHERE state IN ('pending', 'sent');
        ALTER TABLE care_signup ADD CONSTRAINT care_signup_identity_check_id_fkey
            FOREIGN KEY (identity_check_id) REFERENCES identity_check(id);

        COMMENT ON TABLE care_signup IS
            'One online signup (self-care, issue 377): email verified with a code, the person and service address, the identity check, the first payment and the eSIM line; resumable, it keeps the ids of what it created.';
        COMMENT ON COLUMN care_signup.email IS
            'The email the signup started with, lower case; the self-care login gets it.';
        COMMENT ON COLUMN care_signup.state IS
            'started (email not verified), identityPending (the check is with the adapter), identityPassed (pay; a declined card stays here), identityFailed, identityReview (an operator decides), paid (the first top-up is confirmed), activating (the order waits for the network), active, failed (failure_code says why), expired (24 hours without progress before payment).';
        COMMENT ON COLUMN care_signup.client_address IS
            'The address the signup came from: the limit of 10 signups an hour per address.';
        COMMENT ON COLUMN care_signup.email_code_hash IS
            'scrypt hash of the 6-digit verify_email code; 30 minutes, 5 attempts.';
        COMMENT ON COLUMN care_signup.service_address IS
            'The normalized US service address (taxes.md); the billing account gets it.';
        COMMENT ON COLUMN care_signup.identity_reason IS
            'The adapter''s reason for a failed or in-review check, or the operator''s for a decision.';
        COMMENT ON COLUMN care_signup.decided_by IS
            'The operator who approved or rejected a signup in review.';
        COMMENT ON COLUMN care_signup.payment_command_id IS
            'The first-charge card top-up in flight; cleared when the gateway declines it (the customer pays again).';
        COMMENT ON COLUMN care_signup.payment_error IS
            'Why the last first-charge top-up was declined.';
        COMMENT ON COLUMN care_signup.failure_code IS
            'Why the signup failed: the order''s error code (numberUnavailable, simUnavailable, ...).';
        COMMENT ON COLUMN care_signup.activation_shown_at IS
            'When GET returned the eSIM activation code (it is returned once).';
        COMMENT ON COLUMN care_signup.updated_at IS
            'The last progress: a signup before payment expires 24 hours after it.';

        COMMENT ON TABLE identity_check IS
            'Durable outbound identity check of a signup; sent and polled by the billing worker through the identity adapter, never inside a request.';
        COMMENT ON COLUMN identity_check.id IS
            'Idempotency key sent to the identity service.';
        COMMENT ON COLUMN identity_check.state IS
            'pending (to send, or retried after a temporary error), sent (the service has it and has not decided), passed, failed, review (a person must look), exhausted (temporary errors used up the attempts: the signup goes to an operator).';
        COMMENT ON COLUMN identity_check.request IS
            'What is sent: the person and the address; every retry sends it unchanged.';
        COMMENT ON COLUMN identity_check.reason IS
            'The service''s reason for a failed or review verdict.';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE care_signup DROP CONSTRAINT care_signup_identity_check_id_fkey;
        DROP TABLE identity_check;
        DROP TABLE care_signup;
    """)
