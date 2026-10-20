"""Number inventory and clock-derived assignment state (orders own the history)."""

from psycopg import sql

from app.repository._sql import fetch_all, fetch_one, returning, select_by_id, update_by_id


def get(conn, uid, *, lock=False):
    return select_by_id(conn, "subscriber_number", uid, lock="update" if lock else None)


def states(conn, uid, today, quarantine_days):
    row = fetch_one(
        conn,
        """SELECT
          coalesce(bool_or(valid_for @> %(today)s::date), false) AS busy,
          coalesce(bool_or(effective_from > %(today)s::date AND NOT isempty(valid_for)),
                   false) AS reserved,
          coalesce(bool_or(released_on <= %(today)s::date
            AND released_on + %(days)s > %(today)s::date AND NOT isempty(valid_for)),
                   false) AS quarantined
        FROM number_assignment
        WHERE subscriber_number_id = %(uid)s AND cancelled_at IS NULL""",
        {"uid": uid, "today": today, "days": quarantine_days},
    )
    allocation = (
        "allocated"
        if row["busy"]
        else "reserved"
        if row["reserved"]
        else "quarantined"
        if row["quarantined"]
        else "available"
    )
    return {"usageState": "busy" if row["busy"] else "idle", "allocationStatus": allocation}


def create(conn, values):
    return returning(
        conn,
        sql.SQL("INSERT INTO subscriber_number ({}) VALUES ({}) RETURNING *").format(
            sql.SQL(", ").join(map(sql.Identifier, values)),
            sql.SQL(", ").join(sql.Placeholder() for _ in values),
        ),
        list(values.values()),
    )


def update(conn, uid, values):
    return update_by_id(conn, "subscriber_number", uid, values)


def page(conn, where, offset, limit, today, quarantine_days):
    # Derive state before filtering/paging: state filters must also count future claims.
    source = sql.SQL("""WITH inventory AS (
        SELECT n.*, CASE WHEN a.busy THEN 'busy' ELSE 'idle' END AS usage_state,
          CASE WHEN a.busy THEN 'allocated' WHEN a.reserved THEN 'reserved'
               WHEN a.quarantined THEN 'quarantined' ELSE 'available' END AS allocation_status
        FROM subscriber_number n LEFT JOIN LATERAL (
          SELECT bool_or(valid_for @> %s::date) AS busy,
            bool_or(effective_from > %s::date AND NOT isempty(valid_for)) AS reserved,
            bool_or(released_on <= %s::date AND released_on + %s > %s::date
                    AND NOT isempty(valid_for)) AS quarantined
          FROM number_assignment WHERE subscriber_number_id = n.id AND cancelled_at IS NULL
        ) a ON true
    ) """)
    conditions = sql.SQL(" AND ").join(
        sql.SQL("{}::text = %s").format(sql.Identifier(key)) for key in where
    )
    clause = sql.SQL(" WHERE ") + conditions if where else sql.SQL("")
    params = [today, today, today, quarantine_days, today, *map(str, where.values())]
    total = fetch_one(
        conn, source + sql.SQL("SELECT count(*) AS n FROM inventory") + clause, params
    )
    rows = fetch_all(
        conn,
        source
        + sql.SQL("SELECT * FROM inventory")
        + clause
        + sql.SQL(" ORDER BY created_at, id OFFSET %s LIMIT %s"),
        [*params, offset, limit],
    )
    return rows, total["n"]
