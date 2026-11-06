"""Customer notifications: the message log (TMF681) and marketing consent (issue #192).

Revision ID: 0061
Revises: 0060
"""

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE notification_message (
            id uuid PRIMARY KEY,
            event_type text NOT NULL CHECK (event_type <> ''),
            category text NOT NULL CHECK (category IN ('transactional', 'marketing')),
            channel text NOT NULL CHECK (channel IN ('sms', 'email')),
            template text NOT NULL CHECK (template <> ''),
            state text NOT NULL
                CHECK (state IN ('initial', 'inProgress', 'completed', 'cancelled', 'failed')),
            state_reason text,
            receiver_name text,
            receiver_email text,
            receiver_phone text CHECK (receiver_phone ~ '^[1-9][0-9]{7,14}$'),
            customer_id uuid REFERENCES customer(id),
            billing_account_id uuid REFERENCES billing_account(id),
            product_id uuid REFERENCES product(id),
            subject text,
            content text NOT NULL,
            description text,
            priority text,
            sender jsonb CHECK (jsonb_typeof(sender) = 'object'),
            dedupe_key text CHECK (dedupe_key <> ''),
            scheduled_send_time timestamptz,
            send_time timestamptz,
            send_time_complete timestamptz,
            try_times integer NOT NULL DEFAULT 0 CHECK (try_times >= 0),
            next_attempt_at timestamptz,
            last_error text,
            provider_ref text,
            resend_of uuid REFERENCES notification_message(id),
            created_at timestamptz NOT NULL,
            UNIQUE (dedupe_key, channel),
            CHECK ((state = 'cancelled') = (state_reason IS NOT NULL)),
            CHECK (state <> 'completed' OR send_time_complete IS NOT NULL),
            CHECK (state = 'cancelled'
                OR (channel = 'sms' AND receiver_phone IS NOT NULL)
                OR (channel = 'email' AND receiver_email IS NOT NULL))
        );
        CREATE INDEX notification_message_due_idx
            ON notification_message (next_attempt_at) WHERE state IN ('initial', 'inProgress');
        CREATE INDEX notification_message_customer_idx
            ON notification_message (customer_id, created_at DESC);
        CREATE INDEX notification_message_product_idx
            ON notification_message (product_id, created_at DESC);
        CREATE INDEX notification_message_account_idx
            ON notification_message (billing_account_id, created_at DESC);
        COMMENT ON TABLE notification_message IS
            'One customer message (TMF681 CommunicationMessage): what was sent, to whom, on which channel and in which state. Created once per event (dedupe_key) by the notification worker or by an operator, sent through the notify adapter.';
        COMMENT ON COLUMN notification_message.event_type IS
            'The event that caused it (low_balance, renewal_done, grace, usage_80, ...) or operator for a message made through the API.';
        COMMENT ON COLUMN notification_message.category IS
            'transactional (always allowed) or marketing (only with a recorded opt-in on the customer, TCPA).';
        COMMENT ON COLUMN notification_message.template IS
            'The template that rendered the content, <event type>.<channel>; operator for a hand-written message.';
        COMMENT ON COLUMN notification_message.state IS
            'TMF681 state: initial (queued, or waiting for quiet hours to end), inProgress (a send failed, a retry is scheduled), completed (sent), cancelled (not sent: see state_reason), failed (retries used up).';
        COMMENT ON COLUMN notification_message.state_reason IS
            'Why a message was cancelled: noConsent (marketing without opt-in), noRecipient (no address on the channel), operator.';
        COMMENT ON COLUMN notification_message.receiver_phone IS
            'SMS destination as E.164 digits without plus: the customer''s phone contact medium, else the line''s MSISDN.';
        COMMENT ON COLUMN notification_message.dedupe_key IS
            'Identity of the event the message is for (for example usage:<product>:<period>:<allowance>:<80|100>): the unique key with the channel makes a repeated worker tick create nothing twice. Null for an operator message and for a resend.';
        COMMENT ON COLUMN notification_message.scheduled_send_time IS
            'Earliest send time: the end of the SMS quiet hours for a message created inside them.';
        COMMENT ON COLUMN notification_message.send_time IS 'When the first send attempt was made.';
        COMMENT ON COLUMN notification_message.send_time_complete IS 'When the adapter accepted the message.';
        COMMENT ON COLUMN notification_message.try_times IS 'Send attempts made so far.';
        COMMENT ON COLUMN notification_message.next_attempt_at IS
            'When the worker sends or retries it (backoff 1, 5, 15, 60 minutes); null once final.';
        COMMENT ON COLUMN notification_message.provider_ref IS 'The adapter''s reference of the accepted message.';
        COMMENT ON COLUMN notification_message.resend_of IS 'The message this one repeats (an operator resend).';
        COMMENT ON COLUMN notification_message.sender IS 'The TMF681 sender as an operator sent it; the adapter uses the configured sender.';

        CREATE TABLE notification_consent (
            id uuid PRIMARY KEY,
            seq bigint GENERATED ALWAYS AS IDENTITY,
            customer_id uuid NOT NULL REFERENCES customer(id),
            channel text NOT NULL CHECK (channel IN ('sms', 'email')),
            category text NOT NULL CHECK (category = 'marketing'),
            opted_in boolean NOT NULL,
            source text NOT NULL CHECK (source <> ''),
            recorded_by text NOT NULL CHECK (recorded_by <> ''),
            recorded_at timestamptz NOT NULL
        );
        CREATE INDEX notification_consent_idx
            ON notification_consent (customer_id, channel, category, recorded_at DESC, seq DESC);
        COMMENT ON TABLE notification_consent IS
            'Append-only history of a customer''s marketing consent per channel (opt-in or opt-out, with its source): the latest row wins and no row means not opted in. Transactional messages need no consent.';
        COMMENT ON COLUMN notification_consent.seq IS 'Insertion order: the tie-break when two rows share a recorded_at.';
        COMMENT ON COLUMN notification_consent.source IS 'How the consent was obtained (for example signup, sms keyword, call).';
        COMMENT ON COLUMN notification_consent.recorded_by IS 'The operator who recorded it.';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM notification_message)
                OR EXISTS (SELECT 1 FROM notification_consent) THEN
                RAISE EXCEPTION 'notification messages and consent must be removed before downgrade';
            END IF;
        END $$;
        DROP TABLE notification_consent;
        DROP TABLE notification_message;
    """)
