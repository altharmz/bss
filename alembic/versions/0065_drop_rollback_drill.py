"""Drop the drill-only table rollback_drill after the live rollback drill passed (issue #259).

Revision ID: 0065
Revises: 0064

0064 created it so the drill (#254) had a schema downgrade to undo; nothing in the app
uses it. The downgrade recreates it empty, exactly as 0064 left it.
"""

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE rollback_drill;")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE rollback_drill (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            note text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        COMMENT ON TABLE rollback_drill IS
            'Drill only (issue #254): gives the live rollback drill a schema downgrade to undo. Unused by the app; a row makes the downgrade refuse. Dropped by 0065.';
        COMMENT ON COLUMN rollback_drill.note IS 'Free text written by the drill operator.';
        COMMENT ON COLUMN rollback_drill.created_at IS 'When the drill row was written.';
    """)
