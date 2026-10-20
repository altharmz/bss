"""Subscriber-number inventory and effective-dated assignment history.

Revision ID: 0032
Revises: 0031
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        -- TMF639 ResourceAdministrativeStateType, verbatim (including shuttingDown).
        CREATE TYPE resource_administrative_state_type AS ENUM
            ('locked', 'unlocked', 'shuttingDown');
        CREATE TABLE subscriber_number (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            value text NOT NULL UNIQUE CHECK (value ~ '^[1-9][0-9]{7,14}$'),
            administrative_state resource_administrative_state_type NOT NULL DEFAULT 'unlocked',
            resource_specification_id text NOT NULL DEFAULT 'msisdn'
                CHECK (resource_specification_id = 'msisdn'),
            name text NOT NULL,
            description text,
            attrs jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(attrs) = 'object'),
            tmf_meta jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(tmf_meta) = 'object')
        );
        CREATE TABLE number_assignment (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            audit_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            subscriber_number_id uuid NOT NULL REFERENCES subscriber_number,
            product_id uuid NOT NULL REFERENCES product,
            assigned_by_item_id uuid NOT NULL REFERENCES product_order_item,
            ended_by_item_id uuid REFERENCES product_order_item,
            effective_from date NOT NULL CHECK (isfinite(effective_from)),
            released_on date CHECK (isfinite(released_on) AND released_on >= effective_from),
            valid_for daterange GENERATED ALWAYS AS
                (daterange(effective_from, released_on, '[)')) STORED,
            quarantine_days integer NOT NULL CHECK (quarantine_days >= 0),
            cancelled_at timestamptz,
            cancelled_by_item_id uuid REFERENCES product_order_item,
            CHECK ((released_on IS NULL) = (ended_by_item_id IS NULL)),
            CHECK ((cancelled_at IS NULL) = (cancelled_by_item_id IS NULL)),
            CONSTRAINT number_assignment_number_no_overlap EXCLUDE USING gist
                (subscriber_number_id WITH =, valid_for WITH &&)
                WHERE (cancelled_at IS NULL),
            CONSTRAINT number_assignment_product_no_overlap EXCLUDE USING gist
                (product_id WITH =, valid_for WITH &&)
                WHERE (cancelled_at IS NULL)
        );
        CREATE INDEX ON number_assignment (subscriber_number_id, audit_sequence);
        CREATE INDEX ON number_assignment (product_id, audit_sequence);
        CREATE INDEX ON number_assignment (assigned_by_item_id);
        CREATE INDEX ON number_assignment (ended_by_item_id);
        CREATE INDEX ON number_assignment (cancelled_by_item_id);
        COMMENT ON TABLE subscriber_number IS
            'TMF639 LogicalResource inventory: canonical E.164 digits, built-in msisdn specification.';
        COMMENT ON TABLE number_assignment IS
            'Order-owned number claims; half-open business dates, retained cancelled audit versions.';
    """)
    comments = {
        "subscriber_number": {
            "id": "Resource UUID.",
            "created_at": "Creation instant from the injected clock.",
            "updated_at": "Last edit instant from the injected clock.",
            "value": "Unique E.164 digits without plus or formatting; 8..15 digits, first nonzero.",
            "administrative_state": "TMF639 administrativeState; locking does not end service.",
            "resource_specification_id": "Built-in msisdn reference; no resource catalog href.",
            "name": "TMF639 resource display name.",
            "description": "TMF639 resource description.",
            "attrs": "Schema-validated descriptive TMF639 attributes.",
            "tmf_meta": "TMF polymorphism metadata as sent.",
        },
        "number_assignment": {
            "id": "Assignment audit version UUID.",
            "audit_sequence": "Unique insertion sequence, not transaction commit ordering.",
            "created_at": "Acceptance instant from the injected clock.",
            "updated_at": "Last transition instant from the injected clock.",
            "subscriber_number_id": "Claimed subscriber number; history prevents its deletion.",
            "product_id": "Product realized by this number during valid_for.",
            "assigned_by_item_id": "Order item accepting the claim.",
            "ended_by_item_id": "Order item releasing the number, when released_on is set.",
            "effective_from": "Inclusive first business date; future claims reserve capacity.",
            "released_on": "Exclusive release business date; quarantine begins here.",
            "valid_for": "Generated [effective_from, released_on); same-day claims are empty.",
            "quarantine_days": "Quarantine setting at acceptance; later changes cannot revoke a claim.",
            "cancelled_at": "Cancellation instant; cancelled versions retain audit but claim no range.",
            "cancelled_by_item_id": "Order item whose cancellation voided this audit version.",
        },
    }
    for table, columns in comments.items():
        for column, comment in columns.items():
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{comment}'")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM subscriber_number)
               OR EXISTS (SELECT 1 FROM number_assignment) THEN
                RAISE EXCEPTION 'subscriber-number inventory/history must be removed before downgrade';
            END IF;
        END $$;
        DROP TABLE number_assignment;
        DROP TABLE subscriber_number;
        DROP TYPE resource_administrative_state_type;
    """)
