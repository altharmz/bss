"""Usage records from the network and allowance thresholds (issue #190).

Revision ID: 0058
Revises: 0057
"""

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE usage_record (
            id uuid PRIMARY KEY,
            network_record_id text NOT NULL UNIQUE CHECK (network_record_id <> ''),
            kind text NOT NULL CHECK (kind IN ('data', 'voice', 'sms')),
            quantity bigint NOT NULL CHECK (quantity > 0),
            roaming boolean NOT NULL DEFAULT false,
            throttled boolean NOT NULL DEFAULT false,
            msisdn text CHECK (msisdn ~ '^[1-9][0-9]{7,14}$'),
            imsi text CHECK (imsi ~ '^[0-9]{14,15}$'),
            event_at timestamptz NOT NULL,
            event_date date NOT NULL,
            product_id uuid REFERENCES product(id),
            billing_account_id uuid REFERENCES billing_account(id),
            status text NOT NULL CHECK (status IN ('notRated', 'rejected')),
            rejection_reason text,
            created_at timestamptz NOT NULL,
            CHECK (msisdn IS NOT NULL OR imsi IS NOT NULL OR product_id IS NOT NULL),
            CHECK ((product_id IS NULL) = (billing_account_id IS NULL)),
            CHECK ((status = 'rejected') = (product_id IS NULL)),
            CHECK ((status = 'rejected') = (rejection_reason IS NOT NULL))
        );
        CREATE INDEX usage_record_product_idx ON usage_record (product_id, event_at);
        CREATE INDEX usage_record_product_date_idx ON usage_record (product_id, event_date);
        COMMENT ON TABLE usage_record IS
            'One usage record from the network feed (TMF635 ProductUsage): data, voice or SMS used on a line, stored once per network record id and matched to the product that held the number on the event date. Not rated here (pay-as-you-go rating is a later slice).';
        COMMENT ON COLUMN usage_record.network_record_id IS
            'The network''s own record id: the idempotency key, a resent batch stores nothing twice.';
        COMMENT ON COLUMN usage_record.kind IS 'data, voice or sms (the usage specification).';
        COMMENT ON COLUMN usage_record.quantity IS
            'Data in bytes, voice in seconds, sms in messages; always positive.';
        COMMENT ON COLUMN usage_record.throttled IS
            'The network reported the line as throttled at this record.';
        COMMENT ON COLUMN usage_record.event_at IS 'When the usage happened, from the network.';
        COMMENT ON COLUMN usage_record.event_date IS
            'The business date (billing time zone) of event_at: the date that selects the product and the plan period.';
        COMMENT ON COLUMN usage_record.product_id IS
            'The product holding the MSISDN (or IMSI) on event_date through number assignment history; null when no line held it.';
        COMMENT ON COLUMN usage_record.status IS
            'TMF635 status: notRated (matched, rating comes later) or rejected (no line held the number).';
        COMMENT ON COLUMN usage_record.rejection_reason IS 'noLine when the record could not be matched.';

        CREATE TABLE usage_threshold (
            product_id uuid NOT NULL REFERENCES product(id),
            period_start date NOT NULL,
            allowance text NOT NULL CHECK (allowance IN ('data', 'voice', 'sms')),
            threshold smallint NOT NULL CHECK (threshold IN (80, 100)),
            period_end date NOT NULL CHECK (period_end > period_start),
            usage_record_id uuid NOT NULL REFERENCES usage_record(id),
            used bigint NOT NULL,
            included bigint NOT NULL CHECK (included > 0),
            reached_at timestamptz NOT NULL,
            PRIMARY KEY (product_id, period_start, allowance, threshold)
        );
        COMMENT ON TABLE usage_threshold IS
            'An allowance threshold (80% or 100%) reached by a line in a plan period: one row is one event, once per product, period and allowance. The notifications slice reads it.';
        COMMENT ON COLUMN usage_threshold.period_start IS
            'First date of the funded interval (plan period) the usage was counted in.';
        COMMENT ON COLUMN usage_threshold.used IS
            'Period usage when the threshold was reached, in the usage_record unit.';
        COMMENT ON COLUMN usage_threshold.included IS
            'The allowance at that time in the same unit (MB x 1048576 bytes, minutes x 60 seconds, messages), add-ons included.';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM usage_record) THEN
                RAISE EXCEPTION 'usage records must be removed before downgrade';
            END IF;
        END $$;
        DROP TABLE usage_threshold;
        DROP TABLE usage_record;
    """)
