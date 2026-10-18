"""TMF620 attributes: every attribute of specifications, offerings, prices and catalogs
(gap-analysis slice 6).

- `details` on `product_specification`, `product_offering`, `product_offering_price`,
  `product_catalog`: the attributes that are documents to us (brand, version, attachment,
  productOfferingTerm, tax, popRelationship, relatedParty, ...) as validated against the
  vendored spec and stored as sent. Ordering and billing never read them.
- An offering's `externalIdentifier` list moves into `details` (existing codes are
  copied there as the entry they were answered as); `code` stays, derived from the
  `offeringCode` entry, for uniqueness and the UI.
- Optional now, as in the spec: an offering's `product_specification_id` and `valid_for`
  (NULL: valid at any time), a price's `amount` and `currency` (both or neither). A
  price without an amount cannot be attached to an offering, so billing never sees one.

Revision ID: 0024
Revises: 0023
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

DETAILS = """ADD COLUMN details jsonb NOT NULL DEFAULT '{}'
                CHECK (jsonb_typeof(details) = 'object')"""
TABLES = ("product_specification", "product_offering", "product_offering_price", "product_catalog")


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE product_specification {DETAILS};
        ALTER TABLE product_offering {DETAILS},
            ALTER COLUMN product_specification_id DROP NOT NULL,
            ALTER COLUMN valid_for DROP NOT NULL;
        ALTER TABLE product_offering_price {DETAILS},
            ALTER COLUMN amount DROP NOT NULL,
            ALTER COLUMN currency DROP NOT NULL,
            ADD CONSTRAINT product_offering_price_amount_with_currency
                CHECK ((amount IS NULL) = (currency IS NULL)),
            ADD CONSTRAINT product_offering_price_attached_has_amount
                CHECK (product_offering_id IS NULL OR amount IS NOT NULL);
        ALTER TABLE product_catalog {DETAILS};
        UPDATE product_offering SET details = jsonb_build_object(
            'externalIdentifier', jsonb_build_array(jsonb_build_object(
                'id', code, 'externalIdentifierType', 'offeringCode', 'owner', 'bss',
                '@type', 'ExternalIdentifier')))
        WHERE code IS NOT NULL;
        COMMENT ON COLUMN product_offering.valid_for IS
            'TMF620 validFor; NULL: no validFor sent, orderable at any time.';
        COMMENT ON COLUMN product_offering.code IS
            'The id of the offeringCode entry of details.externalIdentifier (unique).';
    """)
    for table in TABLES:
        op.execute(f"""
            COMMENT ON COLUMN {table}.details IS
                'TMF620 descriptive attributes as sent, validated against the spec;'
                ' ordering and billing never read them.'
        """)


def downgrade() -> None:
    # Fails while an offering has no specification or validFor, or a price no amount:
    # those cannot be expressed before 0024 (delete or complete them first).
    op.execute("""
        ALTER TABLE product_catalog DROP COLUMN details;
        ALTER TABLE product_offering_price
            DROP CONSTRAINT product_offering_price_attached_has_amount,
            DROP CONSTRAINT product_offering_price_amount_with_currency,
            ALTER COLUMN currency SET NOT NULL,
            ALTER COLUMN amount SET NOT NULL,
            DROP COLUMN details;
        ALTER TABLE product_offering
            ALTER COLUMN valid_for SET NOT NULL,
            ALTER COLUMN product_specification_id SET NOT NULL,
            DROP COLUMN details;
        ALTER TABLE product_specification DROP COLUMN details;
    """)
