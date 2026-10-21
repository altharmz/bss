"""Retained command ownership of funding holds; no worker caller yet."""

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE funding_command_hold (
            command_id uuid PRIMARY KEY,
            hold_id uuid NOT NULL UNIQUE,
            billing_account_id uuid NOT NULL,
            FOREIGN KEY (hold_id, billing_account_id)
                REFERENCES funding_hold(id, billing_account_id)
        );
        CREATE INDEX ON funding_command_hold(billing_account_id);
        CREATE TRIGGER funding_command_hold_retained
            BEFORE UPDATE OR DELETE ON funding_command_hold
            FOR EACH ROW EXECUTE FUNCTION funding_hold_retained();
        CREATE TRIGGER credit_account_lock
            BEFORE INSERT ON funding_command_hold
            FOR EACH ROW EXECUTE FUNCTION credit_account_lock();
        COMMENT ON TABLE funding_command_hold IS
            'Retained internal command-to-hold retry identity; no public API or worker caller.';
        COMMENT ON COLUMN funding_command_hold.command_id IS
            'Stable internal command UUID, retained after hold release.';
        COMMENT ON COLUMN funding_command_hold.hold_id IS
            'Single immutable hold owned by this command.';
        COMMENT ON COLUMN funding_command_hold.billing_account_id IS
            'Owning account enforced with the hold by composite foreign key.';
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM funding_command_hold) THEN
                RAISE EXCEPTION 'funding command ownership must be retained at revision 0036';
            END IF;
        END $$;
        DROP TABLE funding_command_hold;
    """)
