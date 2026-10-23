"""The sweep of throwaway databases a cut-short run left behind (tests/scratch_db.py)."""

import time
import uuid

import psycopg

from tests import scratch_db
from tests.conftest import ADMIN_URL, _create_database, _drop_database

NOW = 1_800_000_000


def test_a_name_carries_its_creation_time():
    name = scratch_db.name("test")
    assert name.startswith("vpslab_test_")
    assert scratch_db.stale([name], time.time()) == []
    assert scratch_db.stale([name], time.time() + scratch_db.STALE_AFTER + 1) == [name]


def test_stale_is_old_or_untimed_and_only_ours():
    old = f"vpslab_test_{NOW - scratch_db.STALE_AFTER - 1}_0123abcd"
    young = f"vpslab_template_{NOW - 60}_0123abcd"
    untimed = "vpslab_template_0123abcd"
    names = [old, young, untimed, "app", "postgres", "template1", "other_1000_0123abcd"]
    assert scratch_db.stale(names, NOW) == sorted([old, untimed])


def _exists(name: str) -> bool:
    with psycopg.connect(ADMIN_URL) as conn:
        query = "SELECT 1 FROM pg_database WHERE datname = %s"
        return conn.execute(query, (name,)).fetchone() is not None


def test_the_sweep_drops_stale_databases_but_not_one_in_use_or_a_young_one():
    left = f"vpslab_test_1000_{uuid.uuid4().hex[:8]}"
    in_use = f"vpslab_test_1000_{uuid.uuid4().hex[:8]}"
    young = scratch_db.name("test")
    urls = {name: _create_database(name) for name in (left, in_use, young)}
    try:
        with psycopg.connect(urls[in_use]):
            dropped = scratch_db.drop_stale(ADMIN_URL)
        assert left in dropped and not _exists(left)
        assert in_use not in dropped and _exists(in_use)
        assert young not in dropped and _exists(young)
    finally:
        for name in (left, in_use, young):
            _drop_database(name)
