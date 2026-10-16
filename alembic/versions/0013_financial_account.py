"""TMF666 financial accounts and the billing account's link to one.

A financial account is owned by a party (Individual or Organization) and aggregates the
billing accounts of that party's customers. The link lives on the billing account only;
a trigger keeps the owners equal.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE financial_account (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            party_id uuid NOT NULL REFERENCES party,
            name text NOT NULL CHECK (length(name) > 0),
            description text,
            account_type text CHECK (length(account_type) > 0),
            state account_state NOT NULL DEFAULT 'Active',
            credit_limit numeric(12,2) CHECK (credit_limit >= 0),
            credit_limit_currency char(3) CHECK (credit_limit_currency ~ '^[A-Z]{3}$'),
            CONSTRAINT financial_account_credit_limit_has_currency
                CHECK ((credit_limit IS NULL) = (credit_limit_currency IS NULL)),
            -- target of party_account's (financial_account_id, party_id) FK: same owner
            UNIQUE (id, party_id)
        );
        CREATE INDEX financial_account_party_idx ON financial_account (party_id);
        CREATE TRIGGER financial_account_set_updated_at BEFORE UPDATE ON financial_account
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        COMMENT ON TABLE financial_account IS
            'TMF666 financial accounts: a party''s receivables across its billing accounts.';
        ALTER TABLE billing_account
            ADD COLUMN financial_account_id uuid REFERENCES financial_account;
        CREATE INDEX billing_account_financial_account_idx
            ON billing_account (financial_account_id);
        COMMENT ON COLUMN billing_account.financial_account_id IS
            'Optional TMF666 financialAccount; owned by the party of the account''s customer.';

        CREATE FUNCTION billing_account_financial_owner() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.financial_account_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM financial_account f JOIN customer c ON c.party_id = f.party_id
                WHERE f.id = NEW.financial_account_id AND c.id = NEW.customer_id
            ) THEN
                RAISE EXCEPTION 'financial account % is not owned by the party of customer %',
                    NEW.financial_account_id, NEW.customer_id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER billing_account_financial_owner
            BEFORE INSERT OR UPDATE OF financial_account_id, customer_id ON billing_account
            FOR EACH ROW EXECUTE FUNCTION billing_account_financial_owner();
    """)
    for column, meaning in {
        "id": "Server-generated identifier.",
        "created_at": "Database insertion time; stable list ordering.",
        "updated_at": "Database update time exposed as lastUpdate.",
        "party_id": "Owning party (relatedParty role owner); fixed at creation.",
        "name": "Display name; nonempty.",
        "description": "Optional description.",
        "account_type": "Optional free-text accountType.",
        "state": "Active, Suspended or Closed; a Closed account accepts no new links.",
        "credit_limit": "Optional informational creditLimit value; never enforced.",
        "credit_limit_currency": "Currency of credit_limit; set exactly when it is.",
    }.items():
        op.execute(f"COMMENT ON COLUMN financial_account.{column} IS '{meaning}'")


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER billing_account_financial_owner ON billing_account;
        DROP FUNCTION billing_account_financial_owner();
        ALTER TABLE billing_account DROP COLUMN financial_account_id;
        DROP TABLE financial_account;
    """)
