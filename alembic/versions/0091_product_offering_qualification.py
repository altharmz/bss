"""Product offering qualification (TMF679): one eligibility check before an order (#429).

Revision ID: 0091
Revises: 0090

docs/decisions/qualification.md. One table for both TMF679 tasks, checkProductOfferingQualification
and queryProductOfferingQualification: each is answered synchronously (state `done`) by a dry run
of the order engine and stored as answered, readable by id until the worker's housekeeping
deletes it 30 days after its creation. The billing account is no foreign key: a qualification
never holds up deleting an account created by mistake.
"""

from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE product_offering_qualification (
            id uuid PRIMARY KEY,
            kind text NOT NULL CHECK (kind IN ('check', 'query')),
            billing_account_id uuid NOT NULL,
            state text NOT NULL CHECK (state = 'done'),
            qualification_result text CHECK (qualification_result IN ('qualified', 'unqualified')),
            qualification_date date NOT NULL,
            created_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            items jsonb NOT NULL CHECK (jsonb_typeof(items) = 'array'),
            details jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details) = 'object'),
            base_type text CHECK (base_type <> ''),
            schema_location text CHECK (schema_location <> ''),
            CHECK ((kind = 'check') = (qualification_result IS NOT NULL)),
            CHECK (expires_at > created_at)
        );
        CREATE INDEX product_offering_qualification_kind_idx
            ON product_offering_qualification (kind, created_at DESC, id DESC);
        CREATE INDEX product_offering_qualification_account_idx
            ON product_offering_qualification (billing_account_id, created_at DESC, id DESC);
        CREATE INDEX product_offering_qualification_created_idx
            ON product_offering_qualification (created_at);
        COMMENT ON TABLE product_offering_qualification IS
            'A TMF679 product offering qualification as answered: a check (can this account get these offerings, with this device, number and address?) or a query (which offerings can it get?). Synchronous, a dry run of the order engine; deleted by housekeeping 30 days after creation.';
        COMMENT ON COLUMN product_offering_qualification.kind IS
            'check (checkProductOfferingQualification) or query (queryProductOfferingQualification).';
        COMMENT ON COLUMN product_offering_qualification.billing_account_id IS
            'The billing account qualified (from the items or the search criteria). No foreign key: it never blocks deleting the account.';
        COMMENT ON COLUMN product_offering_qualification.state IS
            'TMF679 TaskStateType; always done: every qualification is answered synchronously.';
        COMMENT ON COLUMN product_offering_qualification.qualification_result IS
            'A check: qualified when every item is, else unqualified. NULL for a query.';
        COMMENT ON COLUMN product_offering_qualification.qualification_date IS
            'The BILLING_TZ day the items were qualified for (an order placed that day).';
        COMMENT ON COLUMN product_offering_qualification.expires_at IS
            'expirationDate: the end of the qualification day; prices, funds and numbers move after it.';
        COMMENT ON COLUMN product_offering_qualification.items IS
            'checkProductOfferingQualificationItem or qualifiedProductOfferingItem, as answered.';
        COMMENT ON COLUMN product_offering_qualification.details IS
            'What the client sent that has no behaviour, as sent: description, channel, note, relatedParty, requestedQualificationCompletionDate, the provide* flags and a query''s searchCriteria.';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE product_offering_qualification")
