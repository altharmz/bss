"""Durable HTTP response journal for Idempotency-Key retries."""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = depends_on = None


def upgrade():
    op.execute(r"""
        CREATE TABLE idempotency_key (
            operator text NOT NULL CHECK (operator <> ''),
            method text NOT NULL CHECK (method IN ('POST', 'PATCH', 'DELETE')),
            route text NOT NULL CHECK (route LIKE '/%'),
            key text NOT NULL CHECK (key ~ '^[\x21-\x7e]{1,255}$'),
            request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
            status_code integer NOT NULL CHECK (status_code BETWEEN 200 AND 299),
            response jsonb,
            location text,
            created_at timestamptz NOT NULL,
            tombstoned_at timestamptz,
            PRIMARY KEY (operator, method, route, key),
            CHECK (tombstoned_at IS NULL OR tombstoned_at >= created_at)
        );
        CREATE INDEX idempotency_key_location ON idempotency_key (location) WHERE location IS NOT NULL;
        CREATE FUNCTION idempotency_key_retained() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.tombstoned_at IS NOT NULL
               OR (to_jsonb(NEW) - 'tombstoned_at') IS DISTINCT FROM (to_jsonb(OLD) - 'tombstoned_at')
               OR NEW.tombstoned_at IS NULL THEN
                RAISE EXCEPTION 'an idempotency key is retained; it can only be tombstoned once'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER idempotency_key_retained BEFORE UPDATE OR DELETE ON idempotency_key
            FOR EACH ROW EXECUTE FUNCTION idempotency_key_retained();
        COMMENT ON TABLE idempotency_key IS
            'Stored success response of a request carrying an Idempotency-Key, per operator, method and route.';
        COMMENT ON COLUMN idempotency_key.operator IS 'Signed-in user or MCP principal that sent the key.';
        COMMENT ON COLUMN idempotency_key.route IS 'Request path the key was used on.';
        COMMENT ON COLUMN idempotency_key.request_hash IS
            'SHA-256 of the normalized query and body; the same key with another request is a 409.';
        COMMENT ON COLUMN idempotency_key.response IS 'Stored response body, replayed unchanged.';
        COMMENT ON COLUMN idempotency_key.location IS 'Stored Location header, if the response had one.';
        COMMENT ON COLUMN idempotency_key.tombstoned_at IS
            'Set once when the created resource was deleted: the key then answers 409 and is never reused.';
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT FROM idempotency_key) THEN
                RAISE EXCEPTION 'idempotency keys must be retained at revision 0046';
            END IF;
        END $$;
        DROP TABLE idempotency_key;
        DROP FUNCTION idempotency_key_retained();
    """)
