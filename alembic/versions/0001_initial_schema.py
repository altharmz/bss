"""initial schema: catalog, parties, accounts, orders, inventory, billing, payments

Written by hand - do not regenerate. Enum values are frozen here on purpose: a migration
must describe the schema as it was when written, not import from app code that may change.
TMF-derived enums copy the spec verbatim (tests/test_enum_lock.py compares them with
spec/); the rest are ours (see CLAUDE.md, "Domain model & decisions").

Revision ID: 0001
Revises:
Create Date: 2026-09-28

"""

from collections.abc import Sequence

from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


ENUMS = [
    # --- TMF-derived, verbatim from spec/ (locked by tests/test_enum_lock.py) ---
    # TMF637 ProductStatusType. Upstream spells the last value 'aborted ' (trailing
    # space) - a known typo; stored trimmed.
    _enum(
        "product_status_type",
        "created",
        "pendingActive",
        "cancelled",
        "active",
        "pendingTerminate",
        "terminated",
        "suspended",
        "aborted",
    ),
    # TMF622 ProductOrderStateType
    _enum(
        "product_order_state_type",
        "acknowledged",
        "rejected",
        "pending",
        "held",
        "inProgress",
        "cancelled",
        "completed",
        "failed",
        "partial",
        "assessingCancellation",
        "pendingCancellation",
        "draft",
        "inProgress.accepted",
    ),
    # TMF622 ProductOrderItemStateType
    _enum(
        "product_order_item_state_type",
        "acknowledged",
        "rejected",
        "pending",
        "held",
        "inProgress",
        "cancelled",
        "completed",
        "failed",
        "partial",
        "assessingCancellation",
        "pendingCancellation",
    ),
    # TMF622 ItemActionType
    _enum("item_action_type", "add", "modify", "delete", "noChange"),
    # TMF678 CustomerBillStateType
    _enum(
        "customer_bill_state_type",
        "new",
        "onHold",
        "validated",
        "sent",
        "settled",
        "partiallyPaid",
    ),
    # --- ours, not TMF (the spec field is a free string) ---
    _enum("lifecycle_status", "In Design", "Active", "Retired"),
    _enum("price_type", "recurring", "oneTime"),
    _enum("recurring_charge_period_type", "monthly"),
    _enum("party_type", "Individual", "Organization"),
    _enum("customer_status", "Created", "Active", "Inactive"),
    _enum("account_state", "Active", "Suspended", "Closed"),
    _enum("payment_status", "done"),
    _enum("charge_type", "recurring", "refund"),
    _enum("billing_run_status", "running", "completed", "failed"),
]

# Creation order; downgrade drops in reverse.
TABLES = [
    "product_specification",
    "product_offering",
    "product_offering_price",
    "party",
    "customer",
    "billing_account",
    "product_order",
    "product_order_item",
    "product",
    "billing_run",
    "customer_bill",
    "applied_customer_billing_rate",
    "payment",
]

AUDIT = """
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()"""


def _currency(col: str = "currency") -> str:
    return f"{col} char(3) NOT NULL CHECK ({col} ~ '^[A-Z]{{3}}$')"


def _tstz_period(col: str = "valid_for") -> str:
    # tstzrange is not canonicalised (unlike daterange), so '(a,b]' would otherwise be
    # storable. Half-open [start, end), start required, NULL end = open-ended.
    return (
        f"{col} tstzrange NOT NULL CHECK (NOT isempty({col}) AND NOT lower_inf({col})"
        f" AND lower_inc({col}) AND NOT upper_inc({col}))"
    )


def _date_period(col: str) -> str:
    # daterange is canonicalised to [start, end) by Postgres; require both bounds.
    return (
        f"{col} daterange NOT NULL"
        f" CHECK (NOT isempty({col}) AND NOT lower_inf({col}) AND NOT upper_inf({col}))"
    )


def upgrade() -> None:
    bind = op.get_bind()
    # Trusted extension since PG13: the database owner may create it without superuser
    # (proved by tests/test_nonsuperuser_migration.py).
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    for enum in ENUMS:
        enum.create(bind, checkfirst=True)

    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END $$
        """
    )

    # --- catalog (TMF620) ---
    op.execute(
        f"""
        CREATE TABLE product_specification ({AUDIT},
            name text NOT NULL,
            description text,
            lifecycle_status lifecycle_status NOT NULL
        )"""
    )
    op.execute(
        f"""
        CREATE TABLE product_offering ({AUDIT},
            product_specification_id uuid NOT NULL REFERENCES product_specification,
            name text NOT NULL,
            description text,
            lifecycle_status lifecycle_status NOT NULL,
            is_sellable boolean NOT NULL DEFAULT true,
            {_tstz_period()}
        )"""
    )
    op.execute("CREATE INDEX ON product_offering (product_specification_id)")
    op.execute(
        f"""
        CREATE TABLE product_offering_price ({AUDIT},
            product_offering_id uuid NOT NULL REFERENCES product_offering,
            name text,
            price_type price_type NOT NULL,
            recurring_period recurring_charge_period_type,
            -- mirrors TMF620 recurringChargePeriodLength; widening is a CHECK change
            recurring_period_length smallint NOT NULL DEFAULT 1
                CHECK (recurring_period_length = 1),
            amount numeric(12,2) NOT NULL CHECK (amount >= 0),
            {_currency()},
            {_tstz_period()},
            CONSTRAINT product_offering_price_recurring_has_period
                CHECK ((price_type = 'recurring') = (recurring_period IS NOT NULL)),
            CONSTRAINT product_offering_price_no_overlap EXCLUDE USING gist (
                product_offering_id WITH =, price_type WITH =, currency WITH =,
                valid_for WITH &&)
        )"""
    )

    # --- parties, customers, accounts (TMF632, TMF629, TMF666) ---
    op.execute(
        f"""
        CREATE TABLE party ({AUDIT},
            party_type party_type NOT NULL,
            given_name text,
            family_name text,
            organization_name text,
            CONSTRAINT party_individual_has_family_name
                CHECK (party_type <> 'Individual' OR family_name IS NOT NULL),
            CONSTRAINT party_organization_has_name
                CHECK (party_type <> 'Organization' OR organization_name IS NOT NULL)
        )"""
    )
    op.execute(
        f"""
        CREATE TABLE customer ({AUDIT},
            party_id uuid NOT NULL REFERENCES party,
            status customer_status NOT NULL
        )"""
    )
    op.execute("CREATE INDEX ON customer (party_id)")
    op.execute(
        f"""
        CREATE TABLE billing_account ({AUDIT},
            customer_id uuid NOT NULL REFERENCES customer,
            name text NOT NULL,
            state account_state NOT NULL,
            {_currency()},
            bill_cycle_day smallint NOT NULL CHECK (bill_cycle_day BETWEEN 1 AND 28),
            -- target of the (billing_account_id, currency) FKs: one currency per account
            UNIQUE (id, currency)
        )"""
    )
    op.execute("CREATE INDEX ON billing_account (customer_id)")

    # --- ordering (TMF622) ---
    op.execute(
        f"""
        CREATE TABLE product_order ({AUDIT},
            billing_account_id uuid NOT NULL REFERENCES billing_account,
            state product_order_state_type NOT NULL,
            order_date timestamptz NOT NULL,
            requested_start_date date,
            completion_date timestamptz,
            CHECK (completion_date >= order_date)
        )"""
    )
    op.execute("CREATE INDEX ON product_order (billing_account_id)")
    op.execute(
        f"""
        CREATE TABLE product_order_item ({AUDIT},
            product_order_id uuid NOT NULL REFERENCES product_order,
            item_id text NOT NULL,
            action item_action_type NOT NULL,
            state product_order_item_state_type NOT NULL,
            product_offering_id uuid REFERENCES product_offering,
            -- FK to product is added after that table exists (circular reference)
            product_id uuid,
            UNIQUE (product_order_id, item_id),
            CONSTRAINT product_order_item_add_has_offering
                CHECK (action <> 'add' OR product_offering_id IS NOT NULL),
            CONSTRAINT product_order_item_delete_has_product
                CHECK (action <> 'delete' OR product_id IS NOT NULL)
        )"""
    )
    op.execute("CREATE INDEX ON product_order_item (product_id)")
    op.execute(
        """
        CREATE UNIQUE INDEX product_order_item_one_open_delete
            ON product_order_item (product_id)
            WHERE action = 'delete'
              AND state NOT IN ('completed', 'cancelled', 'rejected', 'failed')
        """
    )

    # --- inventory (TMF637) ---
    op.execute(
        f"""
        CREATE TABLE product ({AUDIT},
            billing_account_id uuid NOT NULL REFERENCES billing_account,
            product_offering_id uuid NOT NULL REFERENCES product_offering,
            status product_status_type NOT NULL,
            start_date date NOT NULL,
            -- exclusive: the product is billable on d when start_date <= d < termination_date
            termination_date date,
            created_by_item_id uuid NOT NULL UNIQUE REFERENCES product_order_item,
            terminated_by_item_id uuid UNIQUE REFERENCES product_order_item,
            CHECK (termination_date >= start_date),
            -- target of the ABR (product_id, billing_account_id) FK
            UNIQUE (id, billing_account_id)
        )"""
    )
    op.execute("CREATE INDEX ON product (billing_account_id)")
    op.execute("CREATE INDEX ON product (product_offering_id)")
    op.execute(
        """
        ALTER TABLE product_order_item
            ADD CONSTRAINT product_order_item_product_id_fkey
            FOREIGN KEY (product_id) REFERENCES product
        """
    )

    # --- billing (TMF678) ---
    op.execute(
        f"""
        CREATE TABLE billing_run ({AUDIT},
            run_date date NOT NULL UNIQUE,
            status billing_run_status NOT NULL,
            started_at timestamptz NOT NULL,
            finished_at timestamptz,
            error text,
            CONSTRAINT billing_run_finished_iff_not_running
                CHECK ((status = 'running') = (finished_at IS NULL)),
            CHECK (finished_at >= started_at)
        )"""
    )
    op.execute(
        f"""
        CREATE TABLE customer_bill ({AUDIT},
            billing_account_id uuid NOT NULL,
            {_currency()},
            {_date_period("bill_period")},
            bill_date date NOT NULL,
            state customer_bill_state_type NOT NULL,
            amount_due numeric(12,2) NOT NULL,
            billing_run_id uuid NOT NULL REFERENCES billing_run,
            FOREIGN KEY (billing_account_id, currency)
                REFERENCES billing_account (id, currency),
            CONSTRAINT customer_bill_no_overlap EXCLUDE USING gist (
                billing_account_id WITH =, bill_period WITH &&),
            -- target of the ABR (customer_bill_id, billing_account_id) FK
            UNIQUE (id, billing_account_id)
        )"""
    )
    op.execute("CREATE INDEX ON customer_bill (billing_run_id)")
    op.execute(
        f"""
        CREATE TABLE applied_customer_billing_rate ({AUDIT},
            product_id uuid NOT NULL,
            billing_account_id uuid NOT NULL,
            charge_type charge_type NOT NULL,
            amount numeric(12,2) NOT NULL,
            {_currency()},
            {_date_period("period")},
            billing_run_id uuid NOT NULL REFERENCES billing_run,
            customer_bill_id uuid,
            refunds_rate_id uuid UNIQUE,
            FOREIGN KEY (product_id, billing_account_id)
                REFERENCES product (id, billing_account_id),
            FOREIGN KEY (billing_account_id, currency)
                REFERENCES billing_account (id, currency),
            -- MATCH SIMPLE: not checked while customer_bill_id is NULL (not yet billed)
            FOREIGN KEY (customer_bill_id, billing_account_id)
                REFERENCES customer_bill (id, billing_account_id),
            -- a refund can only reference a charge of the same product
            UNIQUE (id, product_id),
            FOREIGN KEY (refunds_rate_id, product_id)
                REFERENCES applied_customer_billing_rate (id, product_id),
            CONSTRAINT abr_recurring_amount
                CHECK (charge_type <> 'recurring'
                       OR (amount >= 0 AND refunds_rate_id IS NULL)),
            CONSTRAINT abr_refund_amount
                CHECK (charge_type <> 'refund'
                       OR (amount < 0 AND refunds_rate_id IS NOT NULL)),
            CONSTRAINT abr_no_overlap EXCLUDE USING gist (
                product_id WITH =, charge_type WITH =, period WITH &&)
        )"""
    )
    op.execute("CREATE INDEX ON applied_customer_billing_rate (billing_account_id)")
    op.execute("CREATE INDEX ON applied_customer_billing_rate (billing_run_id)")
    op.execute("CREATE INDEX ON applied_customer_billing_rate (customer_bill_id)")

    # --- payments (TMF676 v4) ---
    op.execute(
        f"""
        CREATE TABLE payment ({AUDIT},
            billing_account_id uuid NOT NULL,
            {_currency()},
            amount numeric(12,2) NOT NULL CHECK (amount > 0),
            received_at timestamptz NOT NULL,
            status payment_status NOT NULL,
            FOREIGN KEY (billing_account_id, currency)
                REFERENCES billing_account (id, currency)
        )"""
    )
    op.execute("CREATE INDEX ON payment (billing_account_id)")

    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_set_updated_at BEFORE UPDATE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Break the product_order_item <-> product cycle first.
    op.execute(
        "ALTER TABLE product_order_item DROP CONSTRAINT product_order_item_product_id_fkey"
    )
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION set_updated_at()")
    # Every type must go, or upgrade -> downgrade -> upgrade fails with "already exists".
    for enum in reversed(ENUMS):
        enum.drop(bind)
    op.execute("DROP EXTENSION btree_gist")
