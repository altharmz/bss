"""The device (IMEI) of a prepaid line, with its history (issue #194).

Revision ID: 0062
Revises: 0061
"""

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE product_device (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id uuid NOT NULL REFERENCES product(id),
            imei text NOT NULL CHECK (imei ~ '^[0-9]{15}$'),
            compatibility text NOT NULL CHECK (compatibility IN ('compatible', 'unknown')),
            reasons jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(reasons) = 'array'),
            checked_at timestamptz NOT NULL,
            started_at timestamptz NOT NULL,
            ended_at timestamptz,
            recorded_by_item_id uuid NOT NULL REFERENCES product_order_item(id),
            ended_by_item_id uuid REFERENCES product_order_item(id),
            CHECK ((ended_at IS NULL) = (ended_by_item_id IS NULL)),
            CHECK (ended_at IS NULL OR ended_at >= started_at)
        );
        CREATE UNIQUE INDEX product_device_current_idx
            ON product_device (product_id) WHERE ended_at IS NULL;
        CREATE INDEX product_device_product_idx
            ON product_device (product_id, started_at DESC);
        ALTER TABLE product_order_item
            DROP CONSTRAINT product_order_item_modify_has_product_and_offering,
            ADD CONSTRAINT product_order_item_modify_has_product_and_offering CHECK (
                action <> 'modify' OR state = 'rejected' OR (product_id IS NOT NULL AND (
                    (product_status IS NOT NULL AND product_offering_id IS NULL
                        AND NOT coalesce(details->'product' ? 'realizingResource', false))
                    OR (product_status IS NULL AND (product_offering_id IS NOT NULL
                        OR coalesce(details->'product' ? 'realizingResource', false)))
                    OR (product_status IS NULL AND product_offering_id IS NULL
                        AND coalesce(details->'product' ? 'productCharacteristic', false)))));
        COMMENT ON CONSTRAINT product_order_item_modify_has_product_and_offering
            ON product_order_item IS
            'A modify item changes the status alone, or the offering, the number or (a prepaid renewal) both, or the device (an imei characteristic) alone.';
        COMMENT ON TABLE product_device IS
            'The devices a prepaid line has had, one row each: the IMEI the order gave and the network''s compatibility answer at that time. The row without ended_at is the current device. Written only by orders (an add item or a device-change modify item); an incompatible device is refused and never stored.';
        COMMENT ON COLUMN product_device.imei IS
            '15 digits, Luhn-checked before it is stored.';
        COMMENT ON COLUMN product_device.compatibility IS
            'The network''s answer (BSS_NETWORK_ADAPTER check_device): compatible, or unknown (the network could not tell or did not answer: allowed with a warning).';
        COMMENT ON COLUMN product_device.reasons IS
            'The network''s reasons (bands, lock, VoLTE) as a JSON array of sentences; empty when compatible.';
        COMMENT ON COLUMN product_device.checked_at IS 'When the network was asked.';
        COMMENT ON COLUMN product_device.started_at IS
            'When the order recorded this device (the order''s clock time).';
        COMMENT ON COLUMN product_device.ended_at IS
            'When a later device change closed this row; null while it is the current device.';
        COMMENT ON COLUMN product_device.recorded_by_item_id IS 'The order item that recorded the device.';
        COMMENT ON COLUMN product_device.ended_by_item_id IS 'The order item whose device change closed the row.';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM product_order_item WHERE action = 'modify'
                    AND state <> 'rejected' AND product_offering_id IS NULL
                    AND product_status IS NULL
                    AND details->'product' ? 'productCharacteristic') THEN
                RAISE EXCEPTION 'device changes must be retained at revision 0062';
            END IF;
        END $$;
        ALTER TABLE product_order_item
            DROP CONSTRAINT product_order_item_modify_has_product_and_offering,
            ADD CONSTRAINT product_order_item_modify_has_product_and_offering CHECK (
                action <> 'modify' OR state = 'rejected' OR (product_id IS NOT NULL AND (
                    (product_status IS NOT NULL AND product_offering_id IS NULL
                        AND NOT coalesce(details->'product' ? 'realizingResource', false))
                    OR (product_status IS NULL AND (product_offering_id IS NOT NULL
                        OR coalesce(details->'product' ? 'realizingResource', false))))));
        COMMENT ON CONSTRAINT product_order_item_modify_has_product_and_offering
            ON product_order_item IS
            'A modify item changes the status alone, or the offering, the number or (a prepaid renewal) both.';
        DROP TABLE product_device;
    """)
