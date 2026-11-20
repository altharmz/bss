"""The audit of a customer's self-care writes (#376).

Revision ID: 0087
Revises: 0086

docs/decisions/self-care.md "The self-care API".

- `account_audit.action` takes `care_top_up`, `care_card_saved`, `care_card_removed`,
  `care_auto_top_up` and `care_add_on`: what a signed-in customer does in self-care.
- `account_audit.verification` takes `session`: the customer's live self-care session.
"""

from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None

ACTIONS = (
    "'sim_swap', 'number_change', 'number_transfer_pin', 'contact_change',"
    " 'port_out', 'pin_set', 'pin_reset', 'lock_set', 'lock_cleared',"
    " 'pin_lockout', 'interaction_logged', 'ticket_opened', 'ticket_status_changed',"
    " 'care_note_added', 'care_sign_in', 'care_locked', 'care_password_changed', 'e911_changed'"
)
NEW_ACTIONS = (
    "'care_top_up', 'care_card_saved', 'care_card_removed', 'care_auto_top_up', 'care_add_on'"
)
VERIFICATIONS = "'pin', 'notRequired', 'none', 'password+code', 'code'"
ACTION_COMMENT = (
    "sim_swap, number_change, number_transfer_pin, contact_change, port_out, pin_set, pin_reset,"
    " lock_set, lock_cleared, pin_lockout, and the care work of issue 199: interaction_logged,"
    " ticket_opened, ticket_status_changed, care_note_added, and self-care of issue 195:"
    " care_sign_in, care_locked, care_password_changed, and the E911 address change of issue"
    " 201: e911_changed."
)
VERIFICATION_COMMENT = (
    "pin (a valid PIN was sent), notRequired (the switch is off), none (no PIN was needed or"
    " sent); for self-care password+code (a sign-in) or code (a password set with an emailed"
    " link code)."
)


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE account_audit DROP CONSTRAINT account_audit_action_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_action_check
            CHECK (action IN ({ACTIONS}, {NEW_ACTIONS}));
        ALTER TABLE account_audit DROP CONSTRAINT account_audit_verification_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_verification_check
            CHECK (verification IN ({VERIFICATIONS}, 'session'));
        COMMENT ON COLUMN account_audit.action IS '{ACTION_COMMENT[:-1]}, and the self-care writes of issue 376: care_top_up, care_card_saved, care_card_removed, care_auto_top_up, care_add_on.';
        COMMENT ON COLUMN account_audit.verification IS '{VERIFICATION_COMMENT[:-1]}, or session (a write in a signed-in self-care session).';
    """)


def downgrade() -> None:
    # the old CHECKs would not hold with the writes' audit entries: a rollback discards them
    op.execute(f"""
        ALTER TABLE account_audit DISABLE TRIGGER account_audit_immutable;
        DELETE FROM account_audit WHERE action IN ({NEW_ACTIONS}) OR verification = 'session';
        ALTER TABLE account_audit ENABLE TRIGGER account_audit_immutable;
        ALTER TABLE account_audit DROP CONSTRAINT account_audit_action_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_action_check
            CHECK (action IN ({ACTIONS}));
        ALTER TABLE account_audit DROP CONSTRAINT account_audit_verification_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_verification_check
            CHECK (verification IN ({VERIFICATIONS}));
        COMMENT ON COLUMN account_audit.action IS '{ACTION_COMMENT}';
        COMMENT ON COLUMN account_audit.verification IS '{VERIFICATION_COMMENT}';
    """)
