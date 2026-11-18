"""The registered E911 address of a Wi-Fi calling line (#201).

Revision ID: 0085
Revises: 0084

docs/decisions/network.md "E911 address".

- `product.e911_address`: the line's registered emergency address (TMF637 `place` of role
  e911Address), normalized; null when none was given.
- `network_command.kind` takes `update_e911` (a TMF641 modify sending a new address alone).
- `account_audit.action` takes `e911_changed` (an address change is a protected action).
- `product_order_item`: a modify item may change the E911 address (`product.place`) alone.
"""

from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None

KINDS = "'activate', 'suspend', 'restore', 'deactivate', 'change_profile', 'swap_sim'"
KIND_COMMENT = (
    "activate (add), suspend, restore (resume), deactivate (terminate), change_profile (offering"
    " change), swap_sim (the line moves to another SIM or eSIM)."
)
ACTIONS = (
    "'sim_swap', 'number_change', 'number_transfer_pin', 'contact_change',"
    " 'port_out', 'pin_set', 'pin_reset', 'lock_set', 'lock_cleared',"
    " 'pin_lockout', 'interaction_logged', 'ticket_opened', 'ticket_status_changed',"
    " 'care_note_added', 'care_sign_in', 'care_locked', 'care_password_changed'"
)
ITEM_CHECK = """
            DROP CONSTRAINT product_order_item_modify_has_product_and_offering,
            ADD CONSTRAINT product_order_item_modify_has_product_and_offering CHECK (
                action <> 'modify' OR state = 'rejected' OR (product_id IS NOT NULL AND (
                    (product_status IS NOT NULL AND product_offering_id IS NULL
                        AND NOT coalesce(details->'product' ? 'realizingResource', false))
                    OR (product_status IS NULL AND (product_offering_id IS NOT NULL
                        OR coalesce(details->'product' ? 'realizingResource', false)))
                    OR (product_status IS NULL AND product_offering_id IS NULL
                        AND coalesce(details->'product' ? 'productCharacteristic', false)){}
                )))"""
ITEM_COMMENT = (
    "A modify item changes the status alone, or the offering, the number or (a prepaid renewal)"
    " both, or the device (an imei characteristic) alone"
)
ACTION_COMMENT = (
    "sim_swap, number_change, number_transfer_pin, contact_change, port_out, pin_set, pin_reset,"
    " lock_set, lock_cleared, pin_lockout, and the care work of issue 199: interaction_logged,"
    " ticket_opened, ticket_status_changed, care_note_added, and self-care of issue 195:"
    " care_sign_in, care_locked, care_password_changed."
)


PLACE_ALONE = """
                    OR (product_status IS NULL AND product_offering_id IS NULL
                        AND coalesce(details->'product' ? 'place', false))"""


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE product ADD COLUMN e911_address jsonb;
        COMMENT ON COLUMN product.e911_address IS
            'The registered E911 address of the line (TMF637 place, role e911Address): a normalized US GeographicAddress (streetNr, streetName, streetSuffix, city, stateOrProvince, postcode, country, geographicSubAddress). Sent to the network while the profile has wifiCalling; changed by an order only once the network confirmed it. Null: none.';

        ALTER TABLE network_command
            DROP CONSTRAINT network_command_kind_check,
            ADD CONSTRAINT network_command_kind_check CHECK (kind IN ({KINDS}, 'update_e911'));
        COMMENT ON COLUMN network_command.kind IS
            '{KIND_COMMENT[:-1]}, update_e911 (a new E911 address alone).';

        ALTER TABLE account_audit DROP CONSTRAINT account_audit_action_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_action_check
            CHECK (action IN ({ACTIONS}, 'e911_changed'));
        COMMENT ON COLUMN account_audit.action IS
            '{ACTION_COMMENT[:-1]}, and the E911 address change of issue 201: e911_changed.';

        ALTER TABLE product_order_item {ITEM_CHECK.format(PLACE_ALONE)};
        COMMENT ON CONSTRAINT product_order_item_modify_has_product_and_offering
            ON product_order_item IS '{ITEM_COMMENT}, or the E911 address (place) alone.';
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM network_command WHERE kind = 'update_e911') THEN
                RAISE EXCEPTION 'update_e911 commands must be removed before downgrade';
            END IF;
        END $$;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM product_order_item WHERE action = 'modify'
                AND state <> 'rejected' AND product_status IS NULL AND product_offering_id IS NULL
                AND details->'product' ? 'place'
                AND NOT details->'product' ? 'realizingResource'
                AND NOT details->'product' ? 'productCharacteristic') THEN
                RAISE EXCEPTION 'E911 address change items must be removed before downgrade';
            END IF;
        END $$;
        ALTER TABLE product_order_item {ITEM_CHECK.format("")};
        COMMENT ON CONSTRAINT product_order_item_modify_has_product_and_offering
            ON product_order_item IS '{ITEM_COMMENT}.';
        ALTER TABLE account_audit DISABLE TRIGGER account_audit_immutable;
        DELETE FROM account_audit WHERE action = 'e911_changed';
        ALTER TABLE account_audit ENABLE TRIGGER account_audit_immutable;
        ALTER TABLE account_audit DROP CONSTRAINT account_audit_action_check;
        ALTER TABLE account_audit ADD CONSTRAINT account_audit_action_check
            CHECK (action IN ({ACTIONS}));
        COMMENT ON COLUMN account_audit.action IS '{ACTION_COMMENT}';
        ALTER TABLE network_command
            DROP CONSTRAINT network_command_kind_check,
            ADD CONSTRAINT network_command_kind_check CHECK (kind IN ({KINDS}));
        COMMENT ON COLUMN network_command.kind IS '{KIND_COMMENT}';
        ALTER TABLE product DROP COLUMN e911_address;
    """)
