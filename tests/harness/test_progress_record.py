"""The progress tracker's record of written tables (tests/conftest.py written_tables,
read by tests/progress_collect.py for `make progress`)."""

import psycopg

from tests.conftest import written_tables


def test_an_update_only_table_counts_as_written(migrated_db_url):
    # prepaid_lifecycle_setting is one row its migration seeds: the template clone counts
    # no insert for it, so it is written only by an update (PUT lifecycle/settings, #276)
    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        conn.execute("SELECT * FROM prepaid_lifecycle_setting").fetchall()
    assert "prepaid_lifecycle_setting" not in written_tables(migrated_db_url)

    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        conn.execute("UPDATE prepaid_lifecycle_setting SET grace_days = 5")
    assert "prepaid_lifecycle_setting" in written_tables(migrated_db_url)


def test_an_inserted_table_still_counts_and_an_untouched_one_does_not(migrated_db_url):
    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        conn.execute("CREATE TABLE progress_probe (id integer)")
        conn.execute("CREATE TABLE progress_untouched (id integer)")
        conn.execute("INSERT INTO progress_probe VALUES (1)")
    written = written_tables(migrated_db_url)
    assert "progress_probe" in written
    assert "progress_untouched" not in written
