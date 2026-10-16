"""TMF666 party and settlement accounts.

One table for both resources: a SettlementAccount is a specialization of PartyAccount
with the same attributes; `kind` keeps them apart (each has its own path). The optional
link to a financial account is a composite foreign key on (financial_account_id,
party_id), so the owners are equal by construction.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE party_account_kind AS ENUM ('PartyAccount', 'SettlementAccount');
        CREATE TABLE party_account (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            kind party_account_kind NOT NULL,
            party_id uuid NOT NULL REFERENCES party,
            name text NOT NULL CHECK (length(name) > 0),
            description text,
            account_type text CHECK (length(account_type) > 0),
            state account_state NOT NULL DEFAULT 'Active',
            credit_limit numeric(12,2) CHECK (credit_limit >= 0),
            credit_limit_currency char(3) CHECK (credit_limit_currency ~ '^[A-Z]{3}$'),
            financial_account_id uuid,
            CONSTRAINT party_account_credit_limit_has_currency
                CHECK ((credit_limit IS NULL) = (credit_limit_currency IS NULL)),
            CONSTRAINT party_account_financial_account_same_owner
                FOREIGN KEY (financial_account_id, party_id)
                REFERENCES financial_account (id, party_id)
        );
        CREATE INDEX party_account_party_idx ON party_account (party_id);
        CREATE INDEX party_account_financial_account_idx ON party_account (financial_account_id);
        CREATE TRIGGER party_account_set_updated_at BEFORE UPDATE ON party_account
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        COMMENT ON TABLE party_account IS
            'TMF666 party and settlement accounts: records only, no ledger, never billed.';
        COMMENT ON TYPE party_account_kind IS
            'Which TMF666 resource a party_account row is; ours, equal to its @type.';
    """)
    for column, meaning in {
        "id": "Server-generated identifier.",
        "created_at": "Database insertion time; stable list ordering.",
        "updated_at": "Database update time exposed as lastUpdate.",
        "kind": "PartyAccount or SettlementAccount; fixed, decides the resource path.",
        "party_id": "Owning party (relatedParty role owner); fixed at creation.",
        "name": "Display name; nonempty.",
        "description": "Optional description.",
        "account_type": "Optional free-text accountType.",
        "state": "Active, Suspended or Closed; no transition rules.",
        "credit_limit": "Optional informational creditLimit value; never enforced.",
        "credit_limit_currency": "Currency of credit_limit; set exactly when it is.",
        "financial_account_id": "Optional TMF666 financialAccount with the same owner.",
    }.items():
        op.execute(f"COMMENT ON COLUMN party_account.{column} IS '{meaning}'")


def downgrade() -> None:
    op.execute("""
        DROP TABLE party_account;
        DROP TYPE party_account_kind;
    """)
