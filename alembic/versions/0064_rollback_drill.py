"""A drill-only table, so the live rollback drill has a schema downgrade to undo (issue #254).

Revision ID: 0064
Revises: 0063

Nothing in the app reads or writes it. Its downgrade refuses while the table has rows, the
way the lossy migrations do (0030, the prepaid 0034-0047), so the drill can also prove
that a refused downgrade is never forced. Migration 0065 drops it after the drill.
"""

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM rollback_drill) THEN
                RAISE EXCEPTION 'rollback_drill has rows: delete them before downgrade';
            END IF;
        END $$;
        DROP TABLE rollback_drill;
    """)
