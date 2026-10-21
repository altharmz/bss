"""Separate operator suspension from prepaid funding expiry."""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE product ADD COLUMN manually_suspended boolean NOT NULL DEFAULT false;
        UPDATE product p SET manually_suspended = true
        FROM billing_account a WHERE a.id = p.billing_account_id
          AND a.rating_type = 'prepaid' AND p.status = 'suspended';
        COMMENT ON COLUMN product.manually_suspended IS
            'Operator suspension independent of prepaid funded coverage and account state.';
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM product WHERE manually_suspended) THEN
                RAISE EXCEPTION 'manual suspension causes must be retained at revision 0043';
            END IF;
        END $$;
        ALTER TABLE product DROP COLUMN manually_suspended;
    """)
