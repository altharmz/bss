"""Saved cards, gateway commands and auto top-up settings (issue #185).

Revision ID: 0054
Revises: 0053
"""

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE payment_method (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            billing_account_id uuid NOT NULL REFERENCES billing_account(id),
            name text NOT NULL,
            description text,
            brand text,
            card_type text,
            last_four text CHECK (last_four ~ '^[0-9]{4}$'),
            expiration_date timestamptz,
            gateway_token text NOT NULL,
            is_default boolean NOT NULL DEFAULT false,
            status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
            created_at timestamptz NOT NULL,
            status_at timestamptz NOT NULL,
            CHECK (NOT is_default OR status = 'active')
        );
        CREATE INDEX payment_method_account_idx ON payment_method (billing_account_id);
        CREATE UNIQUE INDEX payment_method_one_default
            ON payment_method (billing_account_id) WHERE is_default;
        CREATE UNIQUE INDEX payment_method_token_once
            ON payment_method (billing_account_id, gateway_token) WHERE status = 'active';
        COMMENT ON TABLE payment_method IS
            'A saved card as a gateway token (TMF670 tokenizedCard). The BSS never receives or stores a card number.';
        COMMENT ON COLUMN payment_method.gateway_token IS
            'The payment gateway''s token for the card; never returned by the API.';
        COMMENT ON COLUMN payment_method.status IS
            'active, or removed (soft delete: charges already made keep their reference).';
        COMMENT ON COLUMN payment_method.is_default IS
            'TMF670 isPreferred: the card auto top-up and a card top-up without a method use. At most one per account; deleting the default promotes none.';

        CREATE TABLE payment_command (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            billing_account_id uuid NOT NULL REFERENCES billing_account(id),
            payment_method_id uuid NOT NULL REFERENCES payment_method(id),
            kind text NOT NULL DEFAULT 'charge' CHECK (kind IN ('charge', 'refund')),
            purpose text NOT NULL CHECK (purpose IN ('topup', 'auto_topup')),
            amount numeric(12,2) NOT NULL CHECK (amount > 0),
            currency text NOT NULL,
            client_key text,
            state text NOT NULL DEFAULT 'pending' CHECK (state IN
                ('pending', 'sent', 'confirmed', 'declined', 'exhausted', 'cancelled')),
            request jsonb NOT NULL CHECK (jsonb_typeof(request) = 'object'),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            next_attempt_at timestamptz,
            last_error text,
            decline_reason text,
            gateway_ref text,
            payment_id uuid REFERENCES payment(id),
            topup_id uuid REFERENCES balance_action(id),
            created_at timestamptz NOT NULL,
            sent_at timestamptz,
            confirmed_at timestamptz,
            CHECK ((state = 'declined') = (decline_reason IS NOT NULL)),
            CHECK ((state = 'confirmed') = (confirmed_at IS NOT NULL))
        );
        CREATE UNIQUE INDEX payment_command_client_key
            ON payment_command (billing_account_id, client_key) WHERE client_key IS NOT NULL;
        CREATE UNIQUE INDEX payment_command_one_auto_in_flight
            ON payment_command (billing_account_id)
            WHERE purpose = 'auto_topup' AND state IN ('pending', 'sent');
        CREATE INDEX payment_command_open_idx ON payment_command (created_at, id)
            WHERE state IN ('pending', 'sent');
        CREATE INDEX payment_command_account_idx ON payment_command (billing_account_id, created_at);
        COMMENT ON TABLE payment_command IS
            'Durable outbound charge to the payment gateway: sent and retried by the billing worker, never inside a request. The balance rises only when the gateway confirms.';
        COMMENT ON COLUMN payment_command.id IS
            'The idempotency key sent to the gateway.';
        COMMENT ON COLUMN payment_command.client_key IS
            'The caller''s idempotency key of a card top-up: the same key on the same account is the same command.';
        COMMENT ON COLUMN payment_command.state IS
            'pending (to send, or retried after a temporary error), sent (the gateway has it; its result is awaited), confirmed (payment and top-up recorded), declined (final, nothing changed), exhausted (temporary errors used up the attempts; an operator retries), cancelled.';
        COMMENT ON COLUMN payment_command.request IS
            'The charge as first sent (token, amount, currency); every retry resends it unchanged.';
        COMMENT ON COLUMN payment_command.payment_id IS
            'The TMF676 payment recorded when the charge was confirmed.';
        COMMENT ON COLUMN payment_command.topup_id IS
            'The TMF654 top-up recorded with that payment.';

        ALTER TABLE billing_account
            ADD COLUMN auto_topup_enabled boolean NOT NULL DEFAULT false,
            ADD COLUMN auto_topup_threshold numeric(12,2),
            ADD COLUMN auto_topup_amount numeric(12,2),
            ADD COLUMN auto_topup_changed_at timestamptz,
            ADD CONSTRAINT billing_account_auto_topup_settings CHECK (
                NOT auto_topup_enabled OR (auto_topup_threshold > 0
                    AND auto_topup_amount >= auto_topup_threshold));
        COMMENT ON COLUMN billing_account.auto_topup_enabled IS
            'Prepaid only, opt-in: when the spendable balance drops below the threshold, charge the default card for the set amount.';
        COMMENT ON COLUMN billing_account.auto_topup_changed_at IS
            'When the auto top-up settings last changed: a declined automatic charge is not repeated until the balance or the settings change.';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE billing_account
            DROP CONSTRAINT billing_account_auto_topup_settings,
            DROP COLUMN auto_topup_changed_at,
            DROP COLUMN auto_topup_amount,
            DROP COLUMN auto_topup_threshold,
            DROP COLUMN auto_topup_enabled;
        DROP TABLE payment_command;
        DROP TABLE payment_method;
    """)
