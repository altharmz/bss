"""IMSI as a second logical-resource kind beside the MSISDN.

Revision ID: 0049
Revises: 0048
"""

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE subscriber_number
            DROP CONSTRAINT subscriber_number_resource_specification_id_check,
            DROP CONSTRAINT subscriber_number_value_check,
            DROP CONSTRAINT subscriber_number_value_key,
            ADD CONSTRAINT subscriber_number_kind_check
                CHECK (resource_specification_id IN ('msisdn', 'imsi')),
            ADD CONSTRAINT subscriber_number_value_check CHECK (
                (resource_specification_id = 'msisdn' AND value ~ '^[1-9][0-9]{7,14}$')
                OR (resource_specification_id = 'imsi' AND value ~ '^[0-9]{14,15}$')),
            ADD CONSTRAINT subscriber_number_kind_value_key
                UNIQUE (resource_specification_id, value),
            ADD CONSTRAINT subscriber_number_id_kind_key UNIQUE (id, resource_specification_id),
            ADD COLUMN iccid text,
            ADD CONSTRAINT subscriber_number_iccid_check CHECK (
                iccid IS NULL OR (resource_specification_id = 'imsi'
                                  AND iccid ~ '^89[0-9]{16,18}$'));
        CREATE UNIQUE INDEX subscriber_number_iccid_key ON subscriber_number (iccid)
            WHERE iccid IS NOT NULL;
        ALTER TABLE number_assignment
            ADD COLUMN resource_specification_id text NOT NULL DEFAULT 'msisdn';
        ALTER TABLE number_assignment
            ADD CONSTRAINT number_assignment_kind_fkey
                FOREIGN KEY (subscriber_number_id, resource_specification_id)
                REFERENCES subscriber_number (id, resource_specification_id),
            DROP CONSTRAINT number_assignment_product_no_overlap,
            ADD CONSTRAINT number_assignment_product_no_overlap EXCLUDE USING gist
                (product_id WITH =, resource_specification_id WITH =, valid_for WITH &&)
                WHERE (cancelled_at IS NULL);
        ALTER TABLE product ADD COLUMN requires_imsi boolean NOT NULL DEFAULT false;
        COMMENT ON TABLE subscriber_number IS
            'TMF639 LogicalResource inventory: subscriber identities, built-in msisdn (E.164 digits) and imsi specifications.';
        COMMENT ON COLUMN subscriber_number.value IS
            'Digits unique per specification: msisdn 8..15 digits, first nonzero; imsi 14..15 digits.';
        COMMENT ON COLUMN subscriber_number.resource_specification_id IS
            'Built-in msisdn or imsi reference; no resource catalog href.';
        COMMENT ON COLUMN subscriber_number.iccid IS
            'ICCID of the SIM profile (imsi only), optional, unique; changeable only before first assignment.';
        COMMENT ON COLUMN number_assignment.resource_specification_id IS
            'Kind of the claimed resource; at most one live claim per product and kind.';
        COMMENT ON COLUMN product.requires_imsi IS
            'Built-in imsi requirement at ordering; existing products remain unchecked.';
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM subscriber_number WHERE resource_specification_id = 'imsi')
               OR EXISTS (SELECT 1 FROM product WHERE requires_imsi) THEN
                RAISE EXCEPTION 'imsi resources must be removed before downgrade';
            END IF;
        END $$;
        ALTER TABLE product DROP COLUMN requires_imsi;
        ALTER TABLE number_assignment
            DROP CONSTRAINT number_assignment_product_no_overlap,
            ADD CONSTRAINT number_assignment_product_no_overlap EXCLUDE USING gist
                (product_id WITH =, valid_for WITH &&) WHERE (cancelled_at IS NULL),
            DROP CONSTRAINT number_assignment_kind_fkey,
            DROP COLUMN resource_specification_id;
        DROP INDEX subscriber_number_iccid_key;
        ALTER TABLE subscriber_number
            DROP CONSTRAINT subscriber_number_iccid_check,
            DROP COLUMN iccid,
            DROP CONSTRAINT subscriber_number_id_kind_key,
            DROP CONSTRAINT subscriber_number_kind_value_key,
            DROP CONSTRAINT subscriber_number_value_check,
            DROP CONSTRAINT subscriber_number_kind_check,
            ADD CONSTRAINT subscriber_number_value_key UNIQUE (value),
            ADD CONSTRAINT subscriber_number_value_check CHECK (value ~ '^[1-9][0-9]{7,14}$'),
            ADD CONSTRAINT subscriber_number_resource_specification_id_check
                CHECK (resource_specification_id = 'msisdn');
        COMMENT ON TABLE subscriber_number IS
            'TMF639 LogicalResource inventory: canonical E.164 digits, built-in msisdn specification.';
        COMMENT ON COLUMN subscriber_number.value IS
            'Unique E.164 digits without plus or formatting; 8..15 digits, first nonzero.';
        COMMENT ON COLUMN subscriber_number.resource_specification_id IS
            'Built-in msisdn reference; no resource catalog href.';
    """)
