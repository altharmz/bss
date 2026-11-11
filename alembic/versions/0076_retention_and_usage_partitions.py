"""Retention and usage_record partitioning (#282).

Revision ID: 0076
Revises: 0075

Decided by the human 2026-11-09 (docs/decisions/data-model.md "Retention", usage.md
"Partitions"):

- `usage_record` becomes range-partitioned by month on `event_date`. The primary key is
  `(id, event_date)` and the network's idempotency key `UNIQUE (network_record_id,
  event_date)`; a plain index on `network_record_id` finds a resend with another date.
  The table is converted here: new partitioned table, copy, swap, in this one transaction.
- `usage_charge.usage_record_id` and `usage_threshold.usage_record_id` lose their foreign
  keys, so a partition older than 13 months can be dropped while its charges stay.
- `usage_record_partition(month)` creates the partition of a month (idempotent). The
  worker's housekeeping calls it every day for the current and the next two months. Rows
  that reached the DEFAULT partition for that month move into the new partition. Postgres
  refuses storage parameters on a partitioned table ("specify storage parameters for its
  leaf partitions instead"), so the autovacuum settings go on each partition it creates.
- Housekeeping deletes old rows in batches. `idempotency_key` may be deleted only inside a
  housekeeping transaction (`SET LOCAL bss.housekeeping = 'on'`); the API still cannot.
- Indexes for the batches (oldest first) and per-table autovacuum settings on the tables
  that housekeeping deletes from.
"""

from alembic import op
from app.config import settings

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None

AUTOVACUUM = "autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.01"
VACUUMED = ("billing_run_account", "app_session", "event", "event_delivery", "idempotency_key")

COLUMNS = """
    id uuid NOT NULL,
    network_record_id text NOT NULL
        CONSTRAINT usage_record_network_record_id_check CHECK (network_record_id <> ''),
    kind text NOT NULL CONSTRAINT usage_record_kind_check CHECK (kind IN ('data', 'voice', 'sms')),
    quantity bigint NOT NULL CONSTRAINT usage_record_quantity_check CHECK (quantity > 0),
    roaming boolean NOT NULL DEFAULT false,
    throttled boolean NOT NULL DEFAULT false,
    msisdn text CONSTRAINT usage_record_msisdn_check CHECK (msisdn ~ '^[1-9][0-9]{7,14}$'),
    imsi text CONSTRAINT usage_record_imsi_check CHECK (imsi ~ '^[0-9]{14,15}$'),
    event_at timestamptz NOT NULL,
    event_date date NOT NULL,
    product_id uuid CONSTRAINT usage_record_product_id_fkey REFERENCES product(id),
    billing_account_id uuid
        CONSTRAINT usage_record_billing_account_id_fkey REFERENCES billing_account(id),
    status text NOT NULL
        CONSTRAINT usage_record_status_check CHECK (status IN ('notRated', 'rated', 'rejected')),
    rejection_reason text,
    created_at timestamptz NOT NULL,
    CONSTRAINT usage_record_check
        CHECK (msisdn IS NOT NULL OR imsi IS NOT NULL OR product_id IS NOT NULL),
    CONSTRAINT usage_record_check1 CHECK ((product_id IS NULL) = (billing_account_id IS NULL)),
    CONSTRAINT usage_record_check2 CHECK ((status = 'rejected') = (product_id IS NULL)),
    CONSTRAINT usage_record_check3 CHECK ((status = 'rejected') = (rejection_reason IS NOT NULL))
"""

INDEXES = """
    CREATE INDEX usage_record_product_idx ON usage_record (product_id, event_at);
    CREATE INDEX usage_record_product_date_idx ON usage_record (product_id, event_date);
    CREATE INDEX usage_record_product_event_idx ON usage_record (product_id, event_at DESC, id);
"""

PARTITION_FUNCTION = f"""
    CREATE FUNCTION usage_record_partition(month date) RETURNS boolean
    LANGUAGE plpgsql AS $$
    DECLARE
        m_start date := date_trunc('month', month)::date;
        m_end date := (date_trunc('month', month) + interval '1 month')::date;
        part text := 'usage_record_y' || to_char(m_start, 'YYYY') || 'm' || to_char(m_start, 'MM');
    BEGIN
        IF to_regclass(part) IS NOT NULL THEN
            RETURN false;
        END IF;
        -- built beside the table and attached, so rows the DEFAULT partition holds for the
        -- month can move in first (attaching checks that none are left there)
        EXECUTE format('CREATE TABLE %I (LIKE usage_record INCLUDING DEFAULTS'
                       ' INCLUDING CONSTRAINTS) WITH ({AUTOVACUUM})', part);
        IF to_regclass('usage_record_default') IS NOT NULL THEN
            EXECUTE format('WITH moved AS (DELETE FROM usage_record_default'
                           ' WHERE event_date >= %L AND event_date < %L RETURNING *)'
                           ' INSERT INTO %I SELECT * FROM moved', m_start, m_end, part);
        END IF;
        EXECUTE format('ALTER TABLE usage_record ATTACH PARTITION %I FOR VALUES FROM (%L) TO (%L)',
                       part, m_start, m_end);
        EXECUTE format('COMMENT ON TABLE %I IS %L', part,
                       'The usage records of ' || to_char(m_start, 'YYYY-MM')
                       || ' (event_date); dropped by housekeeping after 13 months.');
        RETURN true;
    END $$;
    COMMENT ON FUNCTION usage_record_partition(date) IS
        'Creates the usage_record partition of the month of the date, unless it exists (then false). Called by the worker''s housekeeping for the current and the next two months.';
"""


def retained(*, housekeeping: bool) -> str:
    """idempotency_key_retained: 0046's body, and with housekeeping its one exception."""
    exception = (
        """IF TG_OP = 'DELETE' AND current_setting('bss.housekeeping', true) = 'on' THEN
            RETURN OLD;  -- the worker's housekeeping (#282): keys older than 30 days
        END IF;"""
        if housekeeping
        else ""
    )
    return f"""
    CREATE OR REPLACE FUNCTION idempotency_key_retained() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        {exception}
        IF TG_OP = 'DELETE' OR OLD.tombstoned_at IS NOT NULL
           OR (to_jsonb(NEW) - 'tombstoned_at') IS DISTINCT FROM (to_jsonb(OLD) - 'tombstoned_at')
           OR NEW.tombstoned_at IS NULL THEN
            RAISE EXCEPTION 'an idempotency key is retained; it can only be tombstoned once'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$;
    """


def upgrade() -> None:
    tz = settings.billing_tz.replace("'", "''")
    op.execute(
        """
        ALTER TABLE usage_charge DROP CONSTRAINT usage_charge_usage_record_id_fkey;
        ALTER TABLE usage_threshold DROP CONSTRAINT usage_threshold_usage_record_id_fkey;
        ALTER TABLE usage_record RENAME TO usage_record_old;
        ALTER TABLE usage_record_old RENAME CONSTRAINT usage_record_pkey TO usage_record_old_pkey;
        ALTER TABLE usage_record_old
            RENAME CONSTRAINT usage_record_network_record_id_key TO usage_record_old_network_key;
        DROP INDEX usage_record_product_idx, usage_record_product_date_idx,
                   usage_record_product_event_idx;
        """
    )
    op.execute(f"CREATE TABLE usage_record ({COLUMNS}) PARTITION BY RANGE (event_date)")
    op.execute(PARTITION_FUNCTION)
    # the months of the existing records, through the next two months; then the safety net
    op.execute(
        f"""
        SELECT usage_record_partition(m::date)
          FROM generate_series(
                   date_trunc('month', coalesce((SELECT min(event_date) FROM usage_record_old),
                                                (now() AT TIME ZONE '{tz}')::date)),
                   date_trunc('month', (now() AT TIME ZONE '{tz}')::date) + interval '2 months',
                   interval '1 month') m;
        CREATE TABLE usage_record_default PARTITION OF usage_record DEFAULT WITH ({AUTOVACUUM});
        COMMENT ON TABLE usage_record_default IS
            'Usage records of a month without its partition. Should stay empty: its row count is the bss_usage_record_default_rows gauge.';
        """
    )
    op.execute("INSERT INTO usage_record SELECT * FROM usage_record_old")
    # the indexes after the copy: one build per partition instead of row-by-row upkeep
    op.execute(
        """
        ALTER TABLE usage_record ADD CONSTRAINT usage_record_pkey PRIMARY KEY (id, event_date),
            ADD CONSTRAINT usage_record_network_record_id_event_date_key
                UNIQUE (network_record_id, event_date);
        CREATE INDEX usage_record_network_record_idx ON usage_record (network_record_id);
        """
        + INDEXES
    )
    op.execute(
        """
        DO $$ DECLARE c record; BEGIN
            FOR c IN SELECT a.attname, col_description(a.attrelid, a.attnum) AS d
                       FROM pg_attribute a
                      WHERE a.attrelid = 'usage_record_old'::regclass AND a.attnum > 0
                        AND col_description(a.attrelid, a.attnum) IS NOT NULL LOOP
                EXECUTE format('COMMENT ON COLUMN usage_record.%I IS %L', c.attname, c.d);
            END LOOP;
        END $$;
        DROP TABLE usage_record_old;
        COMMENT ON TABLE usage_record IS
            'One usage record from the network feed (TMF635 ProductUsage): data, voice or SMS used on a line, stored once per network record id and matched to the product that held the number on the event date. Partitioned by month of event_date; partitions older than 13 months are dropped by the worker''s housekeeping (rated amounts stay in usage_charge and the ABRs).';
        COMMENT ON COLUMN usage_record.network_record_id IS
            'The network''s own record id, the idempotency key: unique per event_date, and a resend with another date is found by the plain index and answered with the stored record.';
        COMMENT ON COLUMN usage_charge.usage_record_id IS
            'The rated usage record. No foreign key: the record''s partition is dropped after 13 months and its charge stays as billing evidence.';
        COMMENT ON COLUMN usage_threshold.usage_record_id IS
            'The record that reached the threshold. No foreign key: the record''s partition is dropped after 13 months.';
        """
    )
    op.execute(retained(housekeeping=True))
    op.execute(
        """
        CREATE INDEX idempotency_key_created_idx ON idempotency_key (created_at);
        CREATE INDEX event_delivery_finished_idx ON event_delivery (finished_at)
            WHERE state <> 'pending';
        CREATE INDEX notification_message_created_idx ON notification_message (created_at);
        CREATE INDEX notification_message_resend_idx ON notification_message (resend_of)
            WHERE resend_of IS NOT NULL;
        """
    )
    for table in VACUUMED:
        op.execute(f"ALTER TABLE {table} SET ({AUTOVACUUM})")


def downgrade() -> None:
    for table in VACUUMED:
        op.execute(
            f"ALTER TABLE {table} RESET (autovacuum_vacuum_scale_factor,"
            " autovacuum_analyze_scale_factor)"
        )
    op.execute(
        """
        DROP INDEX idempotency_key_created_idx, event_delivery_finished_idx,
                   notification_message_created_idx, notification_message_resend_idx;
        """
    )
    op.execute(retained(housekeeping=False))
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM usage_charge c
                        WHERE NOT EXISTS (SELECT 1 FROM usage_record r
                                           WHERE r.id = c.usage_record_id))
               OR EXISTS (SELECT 1 FROM usage_threshold t
                           WHERE NOT EXISTS (SELECT 1 FROM usage_record r
                                              WHERE r.id = t.usage_record_id)) THEN
                RAISE EXCEPTION 'usage charges or thresholds refer to dropped usage records:'
                    ' they cannot get their foreign keys back at revision 0075';
            END IF;
        END $$;
        ALTER TABLE usage_record RENAME TO usage_record_new;
        ALTER TABLE usage_record_new RENAME CONSTRAINT usage_record_pkey TO usage_record_new_pkey;
        DROP INDEX usage_record_product_idx, usage_record_product_date_idx,
                   usage_record_product_event_idx, usage_record_network_record_idx;
        """
    )
    op.execute(
        f"""
        CREATE TABLE usage_record ({COLUMNS},
            CONSTRAINT usage_record_pkey PRIMARY KEY (id),
            CONSTRAINT usage_record_network_record_id_key UNIQUE (network_record_id));
        INSERT INTO usage_record SELECT * FROM usage_record_new;
        DO $$ DECLARE c record; BEGIN
            FOR c IN SELECT a.attname, col_description(a.attrelid, a.attnum) AS d
                       FROM pg_attribute a
                      WHERE a.attrelid = 'usage_record_new'::regclass AND a.attnum > 0
                        AND col_description(a.attrelid, a.attnum) IS NOT NULL LOOP
                EXECUTE format('COMMENT ON COLUMN usage_record.%I IS %L', c.attname, c.d);
            END LOOP;
        END $$;
        DROP TABLE usage_record_new;
        DROP FUNCTION usage_record_partition(date);
        {INDEXES}
        COMMENT ON TABLE usage_record IS
            'One usage record from the network feed (TMF635 ProductUsage): data, voice or SMS used on a line, stored once per network record id and matched to the product that held the number on the event date. Not rated here (pay-as-you-go rating is a later slice).';
        COMMENT ON COLUMN usage_record.network_record_id IS
            'The network''s own record id: the idempotency key, a resent batch stores nothing twice.';
        COMMENT ON COLUMN usage_charge.usage_record_id IS NULL;
        COMMENT ON COLUMN usage_threshold.usage_record_id IS NULL;
        ALTER TABLE usage_charge ADD CONSTRAINT usage_charge_usage_record_id_fkey
            FOREIGN KEY (usage_record_id) REFERENCES usage_record(id);
        ALTER TABLE usage_threshold ADD CONSTRAINT usage_threshold_usage_record_id_fkey
            FOREIGN KEY (usage_record_id) REFERENCES usage_record(id);
        """
    )
