"""Self-care: customer logins, their sessions and one-time codes (#195).

Revision ID: 0081
Revises: 0080

docs/decisions/self-care.md. Customers are never `app_user` rows: a login belongs to one
customer, signs in with email, password and an emailed code, and holds a session in its own
table. The audit log takes three actions and one verification for it.
"""

from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None

ACTIONS = (
    "'sim_swap', 'number_change', 'number_transfer_pin', 'contact_change',"
    " 'port_out', 'pin_set', 'pin_reset', 'lock_set', 'lock_cleared',"
    " 'pin_lockout', 'interaction_logged', 'ticket_opened', 'ticket_status_changed',"
    " 'care_note_added'"
)
OLD_ACTION_COMMENT = (
    "sim_swap, number_change, number_transfer_pin, contact_change, port_out, pin_set, pin_reset,"
    " lock_set, lock_cleared, pin_lockout, and the care work of issue 199: interaction_logged,"
    " ticket_opened, ticket_status_changed, care_note_added."
)
OLD_VERIFICATION_COMMENT = (
    "pin (a valid PIN was sent), notRequired (the switch is off), none (no PIN was needed or sent)."
)


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE customer_login (
            id uuid PRIMARY KEY,
            customer_id uuid NOT NULL UNIQUE REFERENCES customer(id),
            email text NOT NULL UNIQUE
                CHECK (email = lower(email) AND email ~ '^[^@[:space:]]+@[^@[:space:]]+$'),
            password_hash text CHECK (password_hash <> ''),
            email_verified_at timestamptz,
            disabled_at timestamptz,
            failed_attempts integer NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
            locked_until timestamptz,
            created_by text NOT NULL CHECK (created_by <> ''),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CHECK (password_hash IS NULL OR email_verified_at IS NOT NULL)
        );
        COMMENT ON TABLE customer_login IS
            'The self-care login of a customer (at most one): email, password and the lockout. Never an app_user: it reaches only /self-care/v1 and only its own customer.';
        COMMENT ON COLUMN customer_login.email IS 'The sign-in name and where the codes go, lower case (unique whatever the case was typed in).';
        COMMENT ON COLUMN customer_login.password_hash IS 'scrypt hash (the format of app_user.password_hash); NULL until the customer sets a password with the emailed link code.';
        COMMENT ON COLUMN customer_login.email_verified_at IS 'When a link code sent to the email was used; a password needs it.';
        COMMENT ON COLUMN customer_login.disabled_at IS 'Set by an operator: no sign-in, every session revoked. A login is disabled, never deleted.';
        COMMENT ON COLUMN customer_login.failed_attempts IS 'Consecutive wrong passwords; a right one resets it, the lockout starts at 10.';
        COMMENT ON COLUMN customer_login.locked_until IS 'While in the future, sign-in is refused even with the right password.';
        COMMENT ON COLUMN customer_login.created_by IS 'The operator who invited the customer, or the signup that created it.';

        CREATE TABLE customer_session (
            token_hash text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{{64}}$'),
            customer_login_id uuid NOT NULL REFERENCES customer_login(id),
            created_at timestamptz NOT NULL,
            last_seen_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            CHECK (expires_at > created_at)
        );
        CREATE INDEX customer_session_login_idx ON customer_session (customer_login_id)
            WHERE revoked_at IS NULL;
        CREATE INDEX customer_session_expires_idx ON customer_session (expires_at);
        COMMENT ON TABLE customer_session IS
            'A signed-in self-care session (cookie bss_care_session). Only the SHA-256 of the random token is stored.';
        COMMENT ON COLUMN customer_session.last_seen_at IS 'Refreshed at most once a minute; the idle expiry counts from it.';
        COMMENT ON COLUMN customer_session.expires_at IS 'The absolute end of the session.';
        COMMENT ON COLUMN customer_session.revoked_at IS 'Signed out, or ended by a password change or a disabled login.';

        CREATE TABLE customer_login_code (
            id uuid PRIMARY KEY,
            customer_login_id uuid NOT NULL REFERENCES customer_login(id),
            purpose text NOT NULL CHECK (purpose IN ('sign_in', 'verify_email', 'reset_password')),
            code_hash text NOT NULL CHECK (code_hash <> ''),
            created_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            used_at timestamptz,
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            CHECK (expires_at > created_at)
        );
        CREATE INDEX customer_login_code_login_idx
            ON customer_login_code (customer_login_id, purpose, created_at DESC)
            WHERE used_at IS NULL;
        CREATE UNIQUE INDEX customer_login_code_link_idx ON customer_login_code (code_hash)
            WHERE purpose <> 'sign_in';
        COMMENT ON TABLE customer_login_code IS
            'One-time codes sent by email: a 6-digit sign-in code (10 minutes, 5 attempts) or a link code that sets a password (verify_email 24 hours, reset_password 1 hour). Only hashes are stored; a new code of a purpose ends the older ones.';
        COMMENT ON COLUMN customer_login_code.code_hash IS 'sign_in: scrypt of the 6 digits; a link code: SHA-256 of its random token (looked up by it).';
        COMMENT ON COLUMN customer_login_code.used_at IS 'When it was used, or superseded by a newer code of the purpose.';
        COMMENT ON COLUMN customer_login_code.attempts IS 'Wrong codes tried against it; at 5 it is spent.';

        ALTER TABLE account_audit DROP CONSTRAINT account_audit_action_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_action_check CHECK (action IN (
            {ACTIONS}, 'care_sign_in', 'care_locked', 'care_password_changed'));
        ALTER TABLE account_audit DROP CONSTRAINT account_audit_verification_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_verification_check
            CHECK (verification IN ('pin', 'notRequired', 'none', 'password+code', 'code'));
        COMMENT ON COLUMN account_audit.action IS '{OLD_ACTION_COMMENT[:-1]}, and self-care of issue 195: care_sign_in, care_locked, care_password_changed.';
        COMMENT ON COLUMN account_audit.verification IS '{OLD_VERIFICATION_COMMENT[:-1]}; for self-care password+code (a sign-in) or code (a password set with an emailed link code).';
    """)


def downgrade() -> None:
    # a rollback discards the logins; their audit entries go too, or the old CHECKs would not hold
    op.execute(f"""
        ALTER TABLE account_audit DISABLE TRIGGER account_audit_immutable;
        DELETE FROM account_audit
            WHERE action IN ('care_sign_in', 'care_locked', 'care_password_changed')
            OR verification IN ('password+code', 'code');
        ALTER TABLE account_audit ENABLE TRIGGER account_audit_immutable;
        ALTER TABLE account_audit DROP CONSTRAINT account_audit_action_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_action_check
            CHECK (action IN ({ACTIONS}));
        ALTER TABLE account_audit DROP CONSTRAINT account_audit_verification_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_verification_check
            CHECK (verification IN ('pin', 'notRequired', 'none'));
        COMMENT ON COLUMN account_audit.action IS '{OLD_ACTION_COMMENT}';
        COMMENT ON COLUMN account_audit.verification IS '{OLD_VERIFICATION_COMMENT}';
        DROP TABLE customer_login_code;
        DROP TABLE customer_session;
        DROP TABLE customer_login;
    """)
