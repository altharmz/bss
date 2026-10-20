"""Relabel RUB as USD without converting amounts.

Revision ID: 0031
Revises: 0030
"""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    # This one-time relabel must include immutable recorded payouts. The table
    # lock and transactional DDL keep the exception confined to this migration.
    conn.exec_driver_sql("ALTER TABLE refund DISABLE TRIGGER refund_immutable")
    # Account currencies participate in composite foreign keys. Defer their checks
    # together, then restore each constraint's original timing before returning.
    constraints = conn.exec_driver_sql("""
        SELECT conrelid::regclass::text, conname, condeferrable, condeferred
        FROM pg_constraint
        WHERE contype = 'f' AND connamespace = 'public'::regnamespace
    """).fetchall()
    quote = conn.dialect.identifier_preparer.quote
    for table, name, _, _ in constraints:
        conn.exec_driver_sql(
            f"ALTER TABLE {table} ALTER CONSTRAINT {quote(name)}"
            " DEFERRABLE INITIALLY DEFERRED"
        )
    conn.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
    # Nested TMF Money documents occur in details, price quotes and event payloads.
    # Only currency fields change; free text and numerical values stay intact.
    conn.exec_driver_sql("""
        CREATE FUNCTION pg_temp.usd_labels(doc jsonb) RETURNS jsonb
        LANGUAGE plpgsql AS $$
        DECLARE result jsonb; k text; v jsonb;
        BEGIN
            IF jsonb_typeof(doc) = 'object' THEN
                result := '{}';
                FOR k, v IN SELECT * FROM jsonb_each(doc) LOOP
                    IF k IN ('unit', 'currency', 'quotedCurrency') AND v = '"RUB"'::jsonb THEN
                        v := '"USD"'::jsonb;
                    ELSE
                        v := pg_temp.usd_labels(v);
                    END IF;
                    result := result || jsonb_build_object(k, v);
                END LOOP;
                RETURN result;
            ELSIF jsonb_typeof(doc) = 'array' THEN
                SELECT coalesce(jsonb_agg(pg_temp.usd_labels(value) ORDER BY ord), '[]')
                INTO result FROM jsonb_array_elements(doc) WITH ORDINALITY AS a(value, ord);
                RETURN result;
            END IF;
            RETURN doc;
        END $$;
    """)
    columns = conn.exec_driver_sql("""
        SELECT table_name, column_name, data_type FROM information_schema.columns
        WHERE table_schema = 'public'
          AND (column_name IN ('currency', 'quoted_currency', 'credit_limit_currency')
               OR data_type = 'jsonb')
        ORDER BY table_name, ordinal_position
    """).fetchall()
    for table, column, data_type in columns:
        target = f"{quote(table)}"
        field = quote(column)
        if data_type == "jsonb":
            conn.exec_driver_sql(
                f"UPDATE {target} SET {field} = pg_temp.usd_labels({field})"
                f" WHERE {field} IS DISTINCT FROM pg_temp.usd_labels({field})"
            )
        else:
            conn.exec_driver_sql(
                f"UPDATE {target} SET {field} = 'USD' WHERE {field} = 'RUB'"
            )
    conn.exec_driver_sql("""
        UPDATE customer_bill SET bill_document = encode(convert_to(
            pg_temp.usd_labels(convert_from(decode(bill_document, 'base64'), 'UTF8')::jsonb)
                ::text, 'UTF8'), 'base64')
        WHERE bill_document IS NOT NULL
          AND convert_from(decode(bill_document, 'base64'), 'UTF8')::jsonb IS DISTINCT FROM
              pg_temp.usd_labels(convert_from(decode(bill_document, 'base64'), 'UTF8')::jsonb)
    """)
    conn.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    for table, name, deferrable, deferred in constraints:
        timing = (
            "DEFERRABLE INITIALLY DEFERRED" if deferred else "DEFERRABLE INITIALLY IMMEDIATE"
        ) if deferrable else "NOT DEFERRABLE"
        conn.exec_driver_sql(f"ALTER TABLE {table} ALTER CONSTRAINT {quote(name)} {timing}")
    conn.exec_driver_sql("DROP FUNCTION pg_temp.usd_labels(jsonb)")
    conn.exec_driver_sql("ALTER TABLE refund ENABLE TRIGGER refund_immutable")


def downgrade() -> None:
    # Existing USD and renamed RUB cannot be distinguished. Keep the labels rather
    # than incorrectly changing original USD records back to RUB.
    pass
