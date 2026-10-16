"""Account bill presentation preferences and immutable issued documents."""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = depends_on = None


def upgrade() -> None:
    for table in ("bill_format", "bill_presentation_media"):
        op.execute(f"""
            CREATE TABLE {table} (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                name text NOT NULL UNIQUE CHECK (length(name) > 0),
                description text
            );
            CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION set_updated_at();
            COMMENT ON TABLE {table} IS 'TMF666 bill presentation configuration.';
        """)
        for column, meaning in {"id": "Server-generated identifier.", "created_at": "Database insertion time.",
            "updated_at": "Database update time.", "name": "Unique presentation name.", "description": "Optional description."}.items():
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{meaning}'")
    op.execute("""
        ALTER TABLE bill_format ADD CHECK (name IN ('JSON detailed', 'JSON summary'));
        ALTER TABLE billing_account ADD COLUMN bill_format_id uuid REFERENCES bill_format(id);
        CREATE TABLE account_presentation_media (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            billing_account_id uuid NOT NULL REFERENCES billing_account(id),
            media_id uuid NOT NULL REFERENCES bill_presentation_media(id),
            UNIQUE (billing_account_id, media_id)
        );
        ALTER TABLE customer_bill ADD COLUMN bill_document text;
        COMMENT ON TABLE account_presentation_media IS 'Account bill delivery preferences; no external delivery is performed.';
        COMMENT ON COLUMN account_presentation_media.id IS 'Membership identifier.';
        COMMENT ON COLUMN account_presentation_media.created_at IS 'Membership insertion time.';
        COMMENT ON COLUMN account_presentation_media.billing_account_id IS 'Owning account.';
        COMMENT ON COLUMN account_presentation_media.media_id IS 'Preferred presentation medium.';
        COMMENT ON COLUMN billing_account.bill_format_id IS 'Optional renderer choice for future bills; default JSON detailed.';
        COMMENT ON COLUMN customer_bill.bill_document IS 'Base64 JSON document snapshotted at issue; NULL for legacy documents.';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE customer_bill DROP COLUMN bill_document;
        DROP TABLE account_presentation_media;
        ALTER TABLE billing_account DROP COLUMN bill_format_id;
        DROP TABLE bill_presentation_media;
        DROP TABLE bill_format;
    """)
