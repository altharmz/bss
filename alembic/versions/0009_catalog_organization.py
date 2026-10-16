"""Categories, catalogs and offering membership (TMF620).

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("category", "product_catalog"):
        op.execute(f"""
            CREATE TABLE {table} (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                name text NOT NULL CHECK (length(name) > 0),
                description text,
                lifecycle_status lifecycle_status NOT NULL DEFAULT 'In Design',
                version text,
                valid_for tstzrange CHECK (valid_for IS NULL OR
                    (NOT isempty(valid_for) AND NOT lower_inf(valid_for)
                     AND lower_inc(valid_for) AND NOT upper_inc(valid_for))),
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        op.execute(f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table}"
                   " FOR EACH ROW EXECUTE FUNCTION set_updated_at()")
        op.execute(f"COMMENT ON TABLE {table} IS 'TMF620 catalog organization resource.'")
        for column, meaning in {
            "id": "Server-generated resource identifier.",
            "name": "Display name; nonempty.",
            "description": "Optional resource description.",
            "lifecycle_status": "Forward-only catalog lifecycle: In Design, Active, Retired.",
            "version": "Optional client-supplied version label.",
            "valid_for": "Optional half-open validity period; organizational metadata only.",
            "created_at": "Database insertion time; stable list ordering.",
            "updated_at": "Database update time exposed as lastUpdate.",
        }.items():
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{meaning}'")
    op.execute("""
        ALTER TABLE category ADD COLUMN parent_id uuid REFERENCES category(id),
            ADD CHECK (parent_id <> id);
        CREATE INDEX category_parent_idx ON category(parent_id);
        COMMENT ON COLUMN category.parent_id IS 'Optional single parent; API serializes hierarchy changes and rejects cycles.';
        ALTER TABLE product_catalog ADD COLUMN catalog_type text;
        COMMENT ON COLUMN product_catalog.catalog_type IS 'Optional catalogType label; this resource groups products.';
        CREATE TABLE catalog_category (
            catalog_id uuid NOT NULL REFERENCES product_catalog(id),
            category_id uuid NOT NULL REFERENCES category(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (catalog_id, category_id)
        );
        CREATE INDEX catalog_category_category_idx ON catalog_category(category_id);
        CREATE TABLE offering_category (
            offering_id uuid NOT NULL REFERENCES product_offering(id),
            category_id uuid NOT NULL REFERENCES category(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (offering_id, category_id)
        );
        CREATE INDEX offering_category_category_idx ON offering_category(category_id);
    """)
    for table, owner in (("catalog_category", "catalog_id"), ("offering_category", "offering_id")):
        op.execute(f"COMMENT ON TABLE {table} IS 'TMF620 many-to-many category membership.'")
        for column, meaning in {
            owner: "Resource owning this membership.",
            "category_id": "Referenced category.",
            "created_at": "Membership insertion time; stable reference ordering.",
        }.items():
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{meaning}'")


def downgrade() -> None:
    for table in ("offering_category", "catalog_category", "product_catalog", "category"):
        op.execute(f"DROP TABLE {table}")
