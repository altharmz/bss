"""TMF677 consumption report snapshots (#427)."""

from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE usage_consumption_report (id uuid PRIMARY KEY, body jsonb NOT NULL)")
    op.execute("COMMENT ON TABLE usage_consumption_report IS 'Usage allowance snapshots returned by TMF677, keyed by report ID.'")


def downgrade() -> None:
    op.execute("DROP TABLE usage_consumption_report")
