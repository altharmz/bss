"""Pay-as-you-go rating of usage against the prepaid balance (issue #191).

- `price_type` gains `usage`: a price per block of a unit of measure on a pay-as-you-go
  offering. `product_offering_price.usage_kind` (data, voice, sms) and `usage_block` (the
  rounding unit in the record unit: bytes, seconds, messages) are set exactly for it, and the
  no-overlap rule is per kind.
- `usage_record.status` gains `rated`; `usage_charge` links each rated record to the pass
  (a `usage` prepaid command), the ABR that charged it and the price applied.
- `applied_customer_billing_rate.usage_command_id`: the rating pass of a usage charge, so a
  late record of a rated day is charged by its own pass (one ABR per product, price, day and
  pass).
- `prepaid_command.kind` gains `usage`.
- `usage_bar`: the barring (`suspend`) and lifting (`restore`) of a pay-as-you-go line the
  balance asks for, sent through the network adapter with their own retry.

Revision ID: 0060
Revises: 0059
"""

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE price_type ADD VALUE IF NOT EXISTS 'usage'")
    op.execute("""
        ALTER TABLE product_offering_price
            ADD COLUMN usage_kind text CHECK (usage_kind IN ('data', 'voice', 'sms')),
            ADD COLUMN usage_block bigint CHECK (usage_block > 0),
            ADD CONSTRAINT product_offering_price_usage_has_unit CHECK (
                (price_type::text = 'usage') = (usage_kind IS NOT NULL)
                AND (usage_kind IS NULL) = (usage_block IS NULL)),
            DROP CONSTRAINT product_offering_price_no_overlap,
            ADD CONSTRAINT product_offering_price_no_overlap EXCLUDE USING gist (
                product_offering_id WITH =, price_type WITH =, currency WITH =,
                (coalesce(usage_kind, '')) WITH =, valid_for WITH &&);
        COMMENT ON COLUMN product_offering_price.price_type IS
            'TMF620 priceType: recurring, oneTime, usage (a price per block of a unit of measure of a pay-as-you-go offering), or an alteration (discount, allowance, penalty: records). Billing charges recurring, oneTime and usage prices.';
        COMMENT ON COLUMN product_offering_price.usage_kind IS
            'For a usage price: the kind it rates, data, voice or sms (from the unit of measure).';
        COMMENT ON COLUMN product_offering_price.usage_block IS
            'For a usage price: the block of the unit of measure in the usage record unit (bytes, seconds, messages); usage is rounded up to whole blocks and each block costs the price.';

        ALTER TABLE usage_record
            DROP CONSTRAINT usage_record_status_check,
            ADD CONSTRAINT usage_record_status_check
                CHECK (status IN ('notRated', 'rated', 'rejected'));
        COMMENT ON COLUMN usage_record.status IS
            'TMF635 status: notRated (matched, not rated: a plan line''s included usage, or waiting for its day to close), rated (a pay-as-you-go line''s usage, charged or without a usage price) or rejected (no line held the number).';

        ALTER TABLE prepaid_command
            DROP CONSTRAINT prepaid_command_kind_check,
            ADD CONSTRAINT prepaid_command_kind_check
                CHECK (kind IN ('order', 'renewal', 'transfer', 'usage'));

        ALTER TABLE applied_customer_billing_rate
            ADD COLUMN usage_command_id uuid REFERENCES prepaid_command(id);
        DROP INDEX abr_fee_once;
        CREATE UNIQUE INDEX abr_fee_once
            ON applied_customer_billing_rate (
                product_id, one_time_price_id, period,
                coalesce(usage_command_id, '00000000-0000-0000-0000-000000000000'::uuid))
            WHERE one_time_price_id IS NOT NULL;
        COMMENT ON COLUMN applied_customer_billing_rate.usage_command_id IS
            'For the charge of rated pay-as-you-go usage (a oneTime rate of a usage price, period = the event day): the rating pass (usage command) that charged it; null for every other rate.';

        CREATE TABLE usage_charge (
            usage_record_id uuid PRIMARY KEY REFERENCES usage_record(id),
            command_id uuid NOT NULL REFERENCES prepaid_command(id),
            billing_account_id uuid NOT NULL REFERENCES billing_account(id),
            product_id uuid NOT NULL REFERENCES product(id),
            price_id uuid REFERENCES product_offering_price(id),
            abr_id uuid REFERENCES applied_customer_billing_rate(id),
            blocks bigint CHECK (blocks > 0),
            created_at timestamptz NOT NULL,
            CHECK ((price_id IS NULL) = (blocks IS NULL))
        );
        CREATE INDEX usage_charge_abr_idx ON usage_charge (abr_id) WHERE abr_id IS NOT NULL;
        CREATE INDEX usage_charge_command_idx ON usage_charge (command_id);
        COMMENT ON TABLE usage_charge IS
            'The rating of one usage record of a pay-as-you-go line: the pass that rated it (a usage command), the usage price applied and the ABR that charged its line, kind and day. The primary key is what keeps a record from ever being charged twice. Without a price (the offering has none for the kind) or with a zero price there is no charge (abr_id null).';
        COMMENT ON COLUMN usage_charge.blocks IS
            'Blocks charged for the whole group of the record (its line, event date and kind in the pass), rounded up once; the same on each record of the group.';
        COMMENT ON COLUMN usage_charge.abr_id IS
            'The oneTime ABR that charged the group of the record; null when nothing was charged.';

        CREATE TABLE usage_bar (
            id uuid PRIMARY KEY,
            product_id uuid NOT NULL REFERENCES product(id),
            seq integer NOT NULL CHECK (seq > 0),
            kind text NOT NULL CHECK (kind IN ('suspend', 'restore')),
            state text NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'confirmed', 'refused', 'cancelled')),
            request jsonb CHECK (jsonb_typeof(request) = 'object'),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            next_attempt_at timestamptz,
            last_error text,
            refusal_code text,
            created_at timestamptz NOT NULL,
            confirmed_at timestamptz,
            UNIQUE (product_id, seq),
            CHECK ((state = 'refused') = (refusal_code IS NOT NULL)),
            CHECK ((state = 'confirmed') = (confirmed_at IS NOT NULL))
        );
        CREATE INDEX usage_bar_pending_idx ON usage_bar (next_attempt_at, id)
            WHERE state = 'pending';
        COMMENT ON TABLE usage_bar IS
            'The barring (suspend) and lifting (restore) a pay-as-you-go line gets from its balance: at zero or below the line is barred on the network, above zero it is restored. One row per command, numbered per product; the latest row is the line''s barring state. Sent by the billing worker through the network adapter; the id is the idempotency key.';
        COMMENT ON COLUMN usage_bar.seq IS 'Order of the commands of the line, from 1; the id derives from product and seq.';
        COMMENT ON COLUMN usage_bar.state IS
            'pending (to send, or retried after a temporary error), confirmed, refused (final: the network said no; nothing is sent again automatically) or cancelled (the line ended before it was sent).';
        COMMENT ON COLUMN usage_bar.request IS 'The request as first sent; every retry resends it unchanged.';
        COMMENT ON COLUMN usage_bar.next_attempt_at IS 'Earliest next attempt after a temporary error.';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM usage_charge)
               OR EXISTS (SELECT FROM usage_bar)
               OR EXISTS (SELECT FROM product_offering_price WHERE usage_kind IS NOT NULL)
               OR EXISTS (SELECT FROM prepaid_command WHERE kind = 'usage')
               OR EXISTS (SELECT FROM usage_record WHERE status = 'rated') THEN
                RAISE EXCEPTION 'rated usage, usage prices and barring must be retained at revision 0060';
            END IF;
        END $$;
        DROP TABLE usage_bar;
        DROP TABLE usage_charge;
        DROP INDEX abr_fee_once;
        CREATE UNIQUE INDEX abr_fee_once
            ON applied_customer_billing_rate (product_id, one_time_price_id, period)
            WHERE one_time_price_id IS NOT NULL;
        ALTER TABLE applied_customer_billing_rate DROP COLUMN usage_command_id;
        ALTER TABLE prepaid_command
            DROP CONSTRAINT prepaid_command_kind_check,
            ADD CONSTRAINT prepaid_command_kind_check
                CHECK (kind IN ('order', 'renewal', 'transfer'));
        ALTER TABLE usage_record
            DROP CONSTRAINT usage_record_status_check,
            ADD CONSTRAINT usage_record_status_check
                CHECK (status IN ('notRated', 'rejected'));
        ALTER TABLE product_offering_price
            DROP CONSTRAINT product_offering_price_no_overlap,
            ADD CONSTRAINT product_offering_price_no_overlap EXCLUDE USING gist (
                product_offering_id WITH =, price_type WITH =, currency WITH =,
                valid_for WITH &&),
            DROP CONSTRAINT product_offering_price_usage_has_unit,
            DROP COLUMN usage_block,
            DROP COLUMN usage_kind;
    """)
