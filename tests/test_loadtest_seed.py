"""The load-test dataset generator (loadtest/seed.py, #289): it refuses anything but
APP_ENV=test, and a run killed in the middle of a day and run again ends with exactly the
rows of a run that was never interrupted."""

from datetime import date

import psycopg
import pytest

from app.config import settings
from app.integrations.network import adapter
from bss_mcp import passwords
from loadtest import seed as generator
from tests import scratch_db
from tests.conftest import _create_database, _drop_database

PASSWORD = "load-test-operator-password"
# 3 days: the kill on day 1 still leaves a whole day after it (4 cost a quarter more, #373)
PARAMS = generator.Params(n=12, days=3, usage_days=1, seed=7, day0=date(2026, 9, 1))


class Killed(Exception):
    pass


@pytest.fixture
def seeding(monkeypatch):
    """What a generator run changes in this process, put back afterwards. The 20
    operators get the smallest scrypt cost (a stored hash names its own, as in
    tests/conftest.py): creating them and their first sign-ins at the production cost was
    half of every run, and no test here is about the cost (#373)."""
    monkeypatch.setattr(passwords, "N", 2)
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "bss_service_secret", settings.bss_service_secret)
    adapter().reset()
    yield
    adapter().reset()


def use(monkeypatch, url: str) -> None:
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setenv("DATABASE_URL", url)  # the `app.users` bootstrap command reads it


def counts(url: str) -> dict[str, int]:
    """Exact rows per table, the generator's own bookkeeping excluded."""
    with psycopg.connect(url) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                " AND tablename NOT LIKE 'loadtest%' AND tablename <> 'alembic_version'"
                " ORDER BY 1"
            )
        ]
        return {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}  # noqa: S608


def test_it_refuses_any_app_env_but_test(monkeypatch, migrated_db_url):
    use(monkeypatch, migrated_db_url)
    for env in ("prod", "staging", "dev"):
        monkeypatch.setattr(settings, "app_env", env)
        with pytest.raises(generator.SeedError, match="APP_ENV"):
            generator.run(PARAMS, password=PASSWORD)
    with psycopg.connect(migrated_db_url) as conn:  # refused before anything was written
        assert conn.execute("SELECT to_regclass('loadtest_progress')").fetchone() == (None,)


@pytest.mark.slow
def test_a_run_killed_mid_day_and_rerun_equals_an_uninterrupted_run(
    monkeypatch, migrated_db_url, template_db, seeding
):
    use(monkeypatch, migrated_db_url)
    report = generator.run(PARAMS, password=PASSWORD)
    whole = counts(migrated_db_url)
    assert report.days_run == PARAMS.days
    # every part of the mix is there
    for table in ("customer_bill", "applied_customer_billing_rate", "payment", "usage_record"):
        assert whole[table] > 0, table
    assert whole["billing_account"] == PARAMS.n
    assert whole["app_user"] == 21
    with psycopg.connect(migrated_db_url) as conn:  # the fixture's cost, on the operators
        costs = conn.execute(
            "SELECT DISTINCT split_part(password_hash, '.', 2) FROM app_user"
            " WHERE username LIKE 'lt-op-%'"
        ).fetchall()
    assert costs == [("2",)]

    name = scratch_db.name("test")
    killed_url = _create_database(name, template=template_db)
    try:
        use(monkeypatch, killed_url)
        adapter().reset()

        def kill(day: int, created: int) -> None:
            if (day, created) == (1, 2):
                raise Killed

        with pytest.raises(Killed):
            generator.run(PARAMS, password=PASSWORD, on_subscriber=kill)
        partial = counts(killed_url)
        assert 0 < partial["billing_account"] < PARAMS.n
        adapter().reset()  # the stub network's memory died with the process
        rerun = generator.run(PARAMS, password=PASSWORD)
        assert rerun.days_run == PARAMS.days - 1  # day 0 was finished before the kill
        assert counts(killed_url) == whole
    finally:
        _drop_database(name)


def test_a_rerun_with_other_parameters_is_refused(monkeypatch, migrated_db_url, seeding):
    use(monkeypatch, migrated_db_url)
    small = generator.Params(n=2, days=1, usage_days=0, seed=7, day0=date(2026, 9, 1))
    generator.run(small, password=PASSWORD)
    other = generator.Params(n=3, days=1, usage_days=0, seed=7, day0=date(2026, 9, 1))
    with pytest.raises(generator.SeedError, match="same parameters"):
        generator.run(other, password=PASSWORD)


def test_the_plan_is_deterministic_and_keeps_clear_of_the_stub_test_numbers():
    params = generator.Params(n=1000, days=90, usage_days=30, seed=289, day0=date(2026, 8, 1))
    first = [generator.subscriber(params, n) for n in range(params.n)]
    assert first == [generator.subscriber(params, n) for n in range(params.n)]
    numbers = [line.msisdn for sub in first for line in sub.lines]
    assert len(set(numbers)) == len(numbers)
    assert not [n for n in numbers if n[-4:] in ("0000", "0001", "0002")]
    assert {sub.day for sub in first} == set(range(30))
    prepaid = sum(sub.prepaid for sub in first) / len(first)
    assert 0.65 < prepaid < 0.75


@pytest.mark.slow
def test_parallel_processes_and_fast_commit_give_the_rows_of_one_process(
    monkeypatch, migrated_db_url, template_db, seeding
):
    # the spawned processes read their settings from the environment: the same gates
    monkeypatch.setenv("BSS_TAX_REQUIRE_SERVICE_ADDRESS", "false")
    monkeypatch.setenv("BSS_ACCOUNT_PIN_REQUIRED", "false")
    params = generator.Params(n=12, days=3, usage_days=1, seed=11, day0=date(2026, 9, 1))
    use(monkeypatch, migrated_db_url)
    generator.run(params, password=PASSWORD)
    serial = counts(migrated_db_url)
    assert serial["usage_record"] > 0

    name = scratch_db.name("test")
    parallel_url = _create_database(name, template=template_db)
    try:
        use(monkeypatch, parallel_url)
        adapter().reset()
        generator.run(params, password=PASSWORD, processes=2, fast_commit=True)
        assert counts(parallel_url) == serial
        with psycopg.connect(parallel_url) as conn:  # the server's default is back
            assert conn.execute(
                "SELECT count(*) FROM pg_db_role_setting s JOIN pg_database d"
                " ON d.oid = s.setdatabase WHERE d.datname = current_database()"
            ).fetchone() == (0,)
    finally:
        _drop_database(name)
