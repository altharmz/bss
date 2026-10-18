"""TMF632/629 attributes: party status and the descriptive attributes of parties and
customers (gap-analysis slice 4).

- `party.status`: TMF632 IndividualStateType (initialized, validated, deceased) or
  OrganizationStateType (initialized, validated, closed); optional, existing parties have
  none. A record only: no status blocks customers, orders or billing.
- `party.details`, `customer.details`: every other attribute of Individual, Organization
  and Customer the specs define (contact media, identifications, characteristics, credit
  profiles, related parties, ...), as validated against the vendored spec and stored as
  sent (date-times in UTC). Billing never reads them.

Revision ID: 0022
Revises: 0021
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE party
            ADD COLUMN status text,
            ADD COLUMN details jsonb NOT NULL DEFAULT '{}'
                CHECK (jsonb_typeof(details) = 'object'),
            ADD CONSTRAINT party_status_of_its_type CHECK (
                status IS NULL
                OR (party_type = 'Individual' AND status IN ('initialized', 'validated', 'deceased'))
                OR (party_type = 'Organization' AND status IN ('initialized', 'validated', 'closed'))
            );
        ALTER TABLE customer
            ADD COLUMN details jsonb NOT NULL DEFAULT '{}'
                CHECK (jsonb_typeof(details) = 'object');
        COMMENT ON COLUMN party.status IS
            'TMF632 status: initialized, validated, deceased (individual) or closed'
            ' (organization); optional, a record only.';
        COMMENT ON COLUMN party.details IS
            'TMF632 descriptive attributes (contactMedium, birthDate, tradingName, ...) as sent,'
            ' validated against the spec; billing never reads them.';
        COMMENT ON COLUMN customer.details IS
            'TMF629 descriptive attributes (description, contactMedium, creditProfile, ...) as'
            ' sent, validated against the spec; billing never reads them.';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE customer DROP COLUMN details;
        ALTER TABLE party
            DROP CONSTRAINT party_status_of_its_type,
            DROP COLUMN details,
            DROP COLUMN status;
    """)
