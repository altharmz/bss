"""International calling and roaming passes (#200).

Revision ID: 0084
Revises: 0083

docs/decisions/usage.md "Usage zones", prepaid.md "Roaming and international passes".

- `usage_record.international`: a voice or SMS record to a non-US destination, as reported.
- `usage_threshold.zone`: thresholds are per usage zone (domestic, roaming, international);
  existing rows are domestic.
- `product_offering_price.usage_zone`: the zone a pay-as-you-go usage price rates (domestic by
  default); the no-overlap rule is per zone too.
- `network_command.purpose`: `order` (the command of an order item, as before) or `passEnd`
  (the change_profile that ends a pass before its plan period ends); one of each per item.
"""

from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None

ZONES = "'domestic', 'roaming', 'international'"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE usage_record ADD COLUMN international boolean NOT NULL DEFAULT false;
        COMMENT ON COLUMN usage_record.international IS
            'A voice or SMS record to a non-US destination, as reported (TMF635 usageCharacteristic international). Its zone: roaming when roaming, else international for voice and SMS, else domestic.';

        ALTER TABLE usage_threshold
            ADD COLUMN zone text NOT NULL DEFAULT 'domestic' CHECK (zone IN ({ZONES})),
            DROP CONSTRAINT usage_threshold_pkey,
            ADD PRIMARY KEY (product_id, period_start, allowance, zone, threshold);
        COMMENT ON COLUMN usage_threshold.zone IS
            'The usage zone of the allowance: domestic, roaming or international.';

        ALTER TABLE product_offering_price
            ADD COLUMN usage_zone text NOT NULL DEFAULT 'domestic' CHECK (usage_zone IN ({ZONES})),
            ADD CONSTRAINT product_offering_price_zone_is_usage
                CHECK (usage_zone = 'domestic' OR price_type::text = 'usage'),
            DROP CONSTRAINT product_offering_price_no_overlap,
            ADD CONSTRAINT product_offering_price_no_overlap EXCLUDE USING gist (
                product_offering_id WITH =, price_type WITH =, currency WITH =,
                (coalesce(usage_kind, '')) WITH =, usage_zone WITH =,
                (coalesce(alteration_lines, 0)) WITH =, valid_for WITH &&);
        COMMENT ON COLUMN product_offering_price.usage_zone IS
            'For a usage price: the usage zone it rates (TMF620 prodSpecCharValueUse usageZone): domestic (the default), roaming or international.';

        ALTER TABLE network_command
            ADD COLUMN purpose text NOT NULL DEFAULT 'order'
                CHECK (purpose IN ('order', 'passEnd')),
            ADD CONSTRAINT network_command_pass_end_is_profile
                CHECK (purpose = 'order' OR kind = 'change_profile'),
            DROP CONSTRAINT network_command_product_order_item_id_key,
            ADD CONSTRAINT network_command_item_purpose_key
                UNIQUE (product_order_item_id, purpose);
        COMMENT ON COLUMN network_command.purpose IS
            'order: the command of its order item; passEnd: the change_profile on the day a pass (an add-on with passDays) ends before its plan period, with the plan profile and the add-ons still live.';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM network_command WHERE purpose <> 'order')
               OR EXISTS (SELECT FROM usage_threshold WHERE zone <> 'domestic')
               OR EXISTS (SELECT FROM product_offering_price WHERE usage_zone <> 'domestic') THEN
                RAISE EXCEPTION 'pass-end commands, zone thresholds and zone prices must be retained at revision 0083';
            END IF;
        END $$;
        ALTER TABLE network_command
            DROP CONSTRAINT network_command_item_purpose_key,
            ADD CONSTRAINT network_command_product_order_item_id_key
                UNIQUE (product_order_item_id),
            DROP CONSTRAINT network_command_pass_end_is_profile,
            DROP COLUMN purpose;
        ALTER TABLE product_offering_price
            DROP CONSTRAINT product_offering_price_no_overlap,
            ADD CONSTRAINT product_offering_price_no_overlap EXCLUDE USING gist (
                product_offering_id WITH =, price_type WITH =, currency WITH =,
                (coalesce(usage_kind, '')) WITH =, (coalesce(alteration_lines, 0)) WITH =,
                valid_for WITH &&),
            DROP CONSTRAINT product_offering_price_zone_is_usage,
            DROP COLUMN usage_zone;
        ALTER TABLE usage_threshold
            DROP CONSTRAINT usage_threshold_pkey,
            ADD PRIMARY KEY (product_id, period_start, allowance, threshold),
            DROP COLUMN zone;
        ALTER TABLE usage_record DROP COLUMN international;
    """)
