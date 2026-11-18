"""Bill PDF documents and the bill email's attachment (#202).

Revision ID: 0086
Revises: 0085

docs/decisions/bills.md "PDF documents and email delivery".

- `customer_bill_document`: the PDF the run renders for a bill it issues (one per bill), with
  its SHA-256 and when it was rendered. Immutable, like the bill's JSON snapshot: a later name
  or address change never rewrites it. Bills issued before this migration have none.
- `notification_message.customer_bill_id`: the bill a message carries as its attachment (the
  `bill_issued` email, and a resend of it).
"""

from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE customer_bill_document (
            customer_bill_id uuid PRIMARY KEY REFERENCES customer_bill(id),
            pdf bytea NOT NULL CHECK (octet_length(pdf) > 0),
            sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            rendered_at timestamptz NOT NULL
        );
        COMMENT ON TABLE customer_bill_document IS
            'The PDF of an issued bill, rendered by the billing run in the bill''s issue transaction and served at customerBill/{id}/document.pdf. Immutable: later name, address or settlement changes never rewrite it. Bills issued before migration 0086 have none.';
        COMMENT ON COLUMN customer_bill_document.pdf IS 'The PDF document (application/pdf).';
        COMMENT ON COLUMN customer_bill_document.sha256 IS 'Lowercase hex SHA-256 of pdf.';
        COMMENT ON COLUMN customer_bill_document.rendered_at IS
            'When it was rendered: the running billing attempt''s started_at, like the bill''s issue.';

        CREATE FUNCTION customer_bill_document_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'customer_bill_document is immutable';
        END $$;
        CREATE TRIGGER customer_bill_document_immutable
            BEFORE UPDATE OR DELETE ON customer_bill_document
            FOR EACH ROW EXECUTE FUNCTION customer_bill_document_immutable();

        ALTER TABLE notification_message
            ADD COLUMN customer_bill_id uuid REFERENCES customer_bill(id);
        COMMENT ON COLUMN notification_message.customer_bill_id IS
            'The bill whose PDF the message carries as an attachment (the bill_issued email and its resends); null for every other message.';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE notification_message DROP COLUMN customer_bill_id;
        DROP TABLE customer_bill_document;
        DROP FUNCTION customer_bill_document_immutable();
    """)
