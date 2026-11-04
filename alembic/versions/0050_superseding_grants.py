"""A mid-period prepaid offering change posts a grant that supersedes the unused rest of
the old one: the grant overlaps only the grant it replaces (issue #151)."""

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE funded_interval
            ADD COLUMN supersedes_interval_id uuid,
            ADD CONSTRAINT funded_interval_id_product_key UNIQUE (id, product_id),
            ADD CONSTRAINT funded_interval_supersedes_key UNIQUE (supersedes_interval_id),
            ADD CONSTRAINT funded_interval_supersedes_fkey
                FOREIGN KEY (supersedes_interval_id, product_id)
                REFERENCES funded_interval (id, product_id),
            ADD CONSTRAINT funded_interval_supersedes_self CHECK (supersedes_interval_id <> id),
            ADD CONSTRAINT funded_interval_supersedes_kind
                CHECK (supersedes_interval_id IS NULL OR command_kind = 'order'),
            DROP CONSTRAINT funded_interval_product_id_anchor_date_interval_index_key,
            DROP CONSTRAINT funded_interval_product_id_period_excl,
            ADD CONSTRAINT funded_interval_product_id_period_excl
                EXCLUDE USING gist (product_id WITH =, period WITH &&)
                WHERE (supersedes_interval_id IS NULL);
        -- a superseding grant restarts the chain, perhaps at the old grant's own anchor
        CREATE UNIQUE INDEX funded_interval_product_id_anchor_date_interval_index_key
            ON funded_interval (product_id, anchor_date, interval_index)
            WHERE supersedes_interval_id IS NULL;
        -- A change dated the old grant's first day charges the new offering's interval from
        -- the same day, perhaps for the same dates: one charge and one refund per product,
        -- offering and period.
        DROP INDEX abr_one_per_start;
        CREATE UNIQUE INDEX abr_one_per_start
            ON applied_customer_billing_rate (product_id, charge_type, period, product_offering_id);
        COMMENT ON INDEX abr_one_per_start IS
            'One charge and one refund per product, offering and period; ranges may overlap or'
            ' share a first day (an offering change refunds the old charge and charges the new one).';
        COMMENT ON COLUMN funded_interval.supersedes_interval_id IS
            'The earlier grant whose unused dates this mid-period offering change credited; only a grant that supersedes another may overlap it.';
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM funded_interval WHERE supersedes_interval_id IS NOT NULL) THEN
                RAISE EXCEPTION 'superseding grants must be retained at revision 0050';
            END IF;
            IF EXISTS (SELECT 1 FROM applied_customer_billing_rate GROUP BY product_id,
                       charge_type, lower(period) HAVING count(*) > 1) THEN
                RAISE EXCEPTION 'charges sharing a first day must be retained at revision 0050';
            END IF;
        END $$;
        DROP INDEX abr_one_per_start;
        CREATE UNIQUE INDEX abr_one_per_start
            ON applied_customer_billing_rate (product_id, charge_type, lower(period));
        COMMENT ON INDEX abr_one_per_start IS
            'One charge and one refund per product and first day; overlapping ranges are'
            ' allowed (an offering change refunds the old charge and charges the new one).';
        DROP INDEX funded_interval_product_id_anchor_date_interval_index_key;
        ALTER TABLE funded_interval
            ADD CONSTRAINT funded_interval_product_id_anchor_date_interval_index_key
                UNIQUE (product_id, anchor_date, interval_index),
            DROP CONSTRAINT funded_interval_product_id_period_excl,
            ADD CONSTRAINT funded_interval_product_id_period_excl
                EXCLUDE USING gist (product_id WITH =, period WITH &&),
            DROP CONSTRAINT funded_interval_supersedes_kind,
            DROP CONSTRAINT funded_interval_supersedes_self,
            DROP CONSTRAINT funded_interval_supersedes_fkey,
            DROP CONSTRAINT funded_interval_supersedes_key,
            DROP CONSTRAINT funded_interval_id_product_key,
            DROP COLUMN supersedes_interval_id;
    """)
