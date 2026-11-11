"""The sweep of throwaway databases a cut-short run left behind (tests/scratch_db.py)."""

import time
import uuid
from pathlib import Path

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


def test_the_sweep_never_takes_a_kept_template():
    kept = f"{scratch_db.KEPT}template_0123456789abcdef"
    assert scratch_db.stale([kept, "vpslab_template_0123abcd"], NOW) == ["vpslab_template_0123abcd"]


def test_the_digest_names_the_migrations_by_path_and_content(tmp_path):
    (tmp_path / "alembic/versions").mkdir(parents=True)
    (tmp_path / "alembic.ini").write_text("[alembic]\n")
    (tmp_path / "alembic/env.py").write_text("env\n")
    first = tmp_path / "alembic/versions/0001_first.py"
    first.write_text("one\n")
    digest = scratch_db.migrations_digest(tmp_path)
    assert digest == scratch_db.migrations_digest(tmp_path)
    assert scratch_db.kept("template", digest) == f"vpslab_kept_template_{digest}"
    # compiled files are not sources
    (tmp_path / "alembic/versions/__pycache__").mkdir()
    (tmp_path / "alembic/versions/__pycache__/0001_first.cpython-312.pyc").write_bytes(b"x")
    assert scratch_db.migrations_digest(tmp_path) == digest
    seen = {digest}
    first.write_text("one, edited\n")
    seen.add(scratch_db.migrations_digest(tmp_path))
    (tmp_path / "alembic/versions/0002_second.py").write_text("two\n")
    seen.add(scratch_db.migrations_digest(tmp_path))
    first.rename(tmp_path / "alembic/versions/0001_renamed.py")
    seen.add(scratch_db.migrations_digest(tmp_path))
    (tmp_path / "alembic.ini").write_text("[alembic]\nchanged = 1\n")
    seen.add(scratch_db.migrations_digest(tmp_path))
    assert len(seen) == 5


def test_the_template_of_a_run_is_the_kept_one_for_the_current_migrations(template_db):
    assert template_db == scratch_db.kept("template", scratch_db.migrations_digest(Path.cwd()))
    assert _exists(template_db)
    with psycopg.connect(ADMIN_URL.rsplit("/", 1)[0] + f"/{template_db}") as conn:
        head = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    assert head is not None


def test_a_kept_database_is_built_once_then_reused_and_replaces_older_ones_of_its_kind():
    # its own kind: the run's real template is of another kind and must survive this test
    kind = f"probe{uuid.uuid4().hex[:6]}"
    older = scratch_db.kept(kind, "0" * 16)
    wanted = scratch_db.kept(kind, "1" * 16)
    built = []

    def build(database: str) -> None:
        assert database.startswith("vpslab_template_")  # a timed name until it is complete
        assert _exists(database) and not _exists(wanted)
        built.append(database)

    _create_database(older)
    try:
        assert scratch_db.keep(ADMIN_URL, kind, "1" * 16, build) == wanted
        assert _exists(wanted) and not _exists(built[0])
        assert not _exists(older)
        assert scratch_db.keep(ADMIN_URL, kind, "1" * 16, build) == wanted
        assert len(built) == 1
    finally:
        for name in (wanted, older):
            _drop_database(name)


def test_an_interrupted_build_leaves_no_kept_database():
    kind = f"probe{uuid.uuid4().hex[:6]}"
    wanted = scratch_db.kept(kind, "2" * 16)
    abandoned = []

    def build(database: str) -> None:
        abandoned.append(database)
        raise RuntimeError("killed")

    try:
        try:
            scratch_db.keep(ADMIN_URL, kind, "2" * 16, build)
        except RuntimeError:
            pass
        assert not _exists(wanted)
        # the half-built database has a timed name: the sweep takes it when it is old
        assert scratch_db.stale(abandoned, time.time() + scratch_db.STALE_AFTER + 1) == abandoned
    finally:
        for name in abandoned:
            _drop_database(name)
