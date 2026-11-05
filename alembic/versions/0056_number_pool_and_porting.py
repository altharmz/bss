"""Number pool, port-in and port-out requests, Number Transfer PINs (issue #189).

Revision ID: 0056
Revises: 0055
"""

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE subscriber_number
            ADD COLUMN origin text NOT NULL DEFAULT 'manual'
                CHECK (origin IN ('manual', 'pool', 'port_in')),
            ADD COLUMN area_code text CHECK (area_code ~ '^[2-9][0-9]{2}$');
        COMMENT ON COLUMN subscriber_number.origin IS
            'Where the number came from: manual (created through TMF639), pool (requested from the carrier by area code) or port_in (brought by the customer).';
        COMMENT ON COLUMN subscriber_number.area_code IS
            'NPA the number was requested or ported with; null for manual numbers.';

        CREATE TABLE port_request (
            id uuid PRIMARY KEY,
            direction text NOT NULL CHECK (direction IN ('in', 'out')),
            state text NOT NULL CHECK (state IN
                ('submitted', 'pending', 'rejected', 'completed', 'cancelled')),
            number_value text NOT NULL CHECK (number_value ~ '^[1-9][0-9]{7,14}$'),
            subscriber_number_id uuid REFERENCES subscriber_number(id),
            carrier_account_number text,
            billing_zip text,
            first_name text,
            last_name text,
            transfer_pin text,
            carrier_reference text,
            submissions integer NOT NULL DEFAULT 1 CHECK (submissions >= 1),
            rejection_code text,
            rejection_reason text,
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            next_attempt_at timestamptz,
            last_error text,
            product_id uuid REFERENCES product(id),
            billing_account_id uuid REFERENCES billing_account(id),
            product_order_id uuid REFERENCES product_order(id),
            answered_at timestamptz,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            completed_at timestamptz,
            CHECK ((state = 'rejected') = (rejection_code IS NOT NULL)),
            CHECK ((state = 'completed') = (completed_at IS NOT NULL)),
            CHECK (direction = 'out' OR subscriber_number_id IS NOT NULL),
            CHECK (direction = 'in' OR state IN ('pending', 'rejected', 'completed')),
            CHECK (direction = 'out' OR answered_at IS NULL),
            CHECK (direction = 'in' OR carrier_reference IS NOT NULL)
        );
        CREATE UNIQUE INDEX port_request_out_reference_key ON port_request (carrier_reference)
            WHERE direction = 'out';
        CREATE UNIQUE INDEX port_request_in_open_key ON port_request (subscriber_number_id)
            WHERE direction = 'in' AND state IN ('submitted', 'pending', 'rejected');
        CREATE INDEX port_request_number_idx ON port_request (number_value);
        CREATE INDEX port_request_inventory_idx ON port_request (subscriber_number_id)
            WHERE subscriber_number_id IS NOT NULL;
        CREATE INDEX port_request_work_idx ON port_request (direction, state);
        COMMENT ON TABLE port_request IS
            'A number port: in (the customer brings a number, submitted to the carrier by the billing worker) or out (the carrier reports a request, validated against the Number Transfer PIN). TMF has no porting API: internal API only.';
        COMMENT ON COLUMN port_request.id IS
            'Request id; the carrier idempotency key of a submission is derived from it and the submission count.';
        COMMENT ON COLUMN port_request.number_value IS
            'The number being ported, E.164 digits; a port-out can name a number that is not in inventory (rejected numberUnknown).';
        COMMENT ON COLUMN port_request.state IS
            'submitted (to send), pending (the carrier has it), rejected (with a code and reason; port-in can be resubmitted), completed, cancelled. A port-out request is decided in the transaction that records it: rejected or completed.';
        COMMENT ON COLUMN port_request.transfer_pin IS
            'Port-in: the losing carrier''s transfer PIN, kept only until the carrier has accepted the submission, then cleared; never returned by any API.';
        COMMENT ON COLUMN port_request.carrier_reference IS
            'The carrier''s reference of the port; for port-out the carrier''s request id (unique).';
        COMMENT ON COLUMN port_request.submissions IS
            'Port-in: how many times the request was submitted (1 + resubmits).';
        COMMENT ON COLUMN port_request.rejection_code IS
            'Why the carrier or the BSS refused: port-in carrier codes, or for port-out numberUnknown, pinInvalid, pinExpired, lineNotActive, terminationRefused.';
        COMMENT ON COLUMN port_request.product_order_id IS
            'Port-out: the termination order that ended the product.';
        COMMENT ON COLUMN port_request.answered_at IS
            'Port-out: when the carrier acknowledged our answer (approved or rejected); null = to send, retried by the worker.';

        CREATE TABLE number_transfer_pin (
            id uuid PRIMARY KEY,
            subscriber_number_id uuid NOT NULL REFERENCES subscriber_number(id),
            product_id uuid NOT NULL REFERENCES product(id),
            pin_hash text NOT NULL,
            created_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            used_at timestamptz,
            revoked_at timestamptz,
            CHECK (expires_at > created_at)
        );
        CREATE INDEX number_transfer_pin_product_idx ON number_transfer_pin (product_id, created_at);
        COMMENT ON TABLE number_transfer_pin IS
            'Number Transfer PINs generated on request for a line''s number; time-limited and single use. The PIN itself is shown once to the agent and never stored.';
        COMMENT ON COLUMN number_transfer_pin.pin_hash IS
            'sha256 over the PIN id and the PIN: the PIN is not stored.';
        COMMENT ON COLUMN number_transfer_pin.expires_at IS
            'Created at plus BSS_NUMBER_TRANSFER_PIN_DAYS; an expired PIN is refused at port-out.';
        COMMENT ON COLUMN number_transfer_pin.revoked_at IS
            'Set when a newer PIN replaced this one.';
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM port_request)
               OR EXISTS (SELECT 1 FROM number_transfer_pin) THEN
                RAISE EXCEPTION 'port requests and transfer PINs must be removed before downgrade';
            END IF;
            IF EXISTS (SELECT 1 FROM subscriber_number WHERE origin <> 'manual') THEN
                RAISE EXCEPTION 'pool and port-in numbers must be removed before downgrade';
            END IF;
        END $$;
        DROP TABLE number_transfer_pin;
        DROP TABLE port_request;
        ALTER TABLE subscriber_number DROP COLUMN area_code, DROP COLUMN origin;
    """)
