"""Shared builders of tests/test_hot_path_indexes.py, used by other test modules (#423)."""


def plan(conn, query, params=()) -> str:
    # an index that can serve it must be chosen, and read in order (the test tables are
    # empty, so a bitmap scan and a sort of nothing would otherwise look as cheap)
    conn.execute("SET enable_seqscan = off")
    conn.execute("SET enable_bitmapscan = off")
    try:
        return "\n".join(r[0] for r in conn.execute("EXPLAIN " + query, params))
    finally:
        conn.execute("RESET enable_seqscan")
        conn.execute("RESET enable_bitmapscan")
