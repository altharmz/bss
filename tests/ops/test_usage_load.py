"""The stub network's load-test usage generator (#281, BSS_STUB_USAGE_RATE): its daily
shape, the records it makes, the refusal outside APP_ENV=test, and the worker's feed step
storing what it makes."""
# ruff: noqa: F811

import re
from datetime import UTC, datetime, timedelta
from functools import partial
from zoneinfo import ZoneInfo

import pytest

from app.clock import FixedClock
from app.config import Settings
from app.integrations import network
from app.integrations.network.stub import StubNetwork
from app.integrations.network.usage_load import (
    MAX_QUEUED,
    LoadRefused,
    UsageLoad,
    assigned_numbers,
    expected,
    factor,
)
from app.worker.__main__ import main as worker_main
from tests.builders.prepaid.prepaid_renewal_producer import tick
from tests.builders.prepaid.usage import MSISDN, line_on
from tests.builders.resources.network_provisioning import stub, world  # noqa: F401 - fixtures

MSK = ZoneInfo("Europe/Moscow")


def local(hour, minute=0):
    return datetime(2026, 11, 10, hour, minute, tzinfo=MSK)


def load(clock, numbers=("13025550100",), rate=23.0, app_env="test"):
    return UsageLoad(
        rate, app_env=app_env, clock=clock, tz=MSK, numbers=lambda _day: list(numbers), seed=1
    )


def test_the_daily_shape_peaks_at_20_and_bottoms_at_04_with_the_average_kept():
    assert 23 * factor(local(20)) == pytest.approx(70, abs=0.5)
    assert 23 * factor(local(4)) == pytest.approx(8, abs=0.5)
    assert max(range(24), key=lambda h: factor(local(h))) == 20
    assert min(range(24), key=lambda h: factor(local(h))) == 4
    # 2M records a day at 23/s
    day = expected(23, local(0), local(0) + timedelta(days=1), MSK)
    assert day == pytest.approx(23 * 86400, rel=1e-9)
    assert 1_980_000 < day < 2_000_000
    # the integral is additive: split at odd instants it gives the same total
    split = expected(23, local(0), local(7, 13), MSK) + expected(
        23, local(7, 13), local(0) + timedelta(days=1), MSK
    )
    assert split == pytest.approx(day, rel=1e-9)


@pytest.mark.slow
def test_records_follow_the_rate_and_name_assigned_numbers():
    clock = FixedClock(local(19, 30))
    gen = load(clock, numbers=("13025550100", "13025550101"))
    clock.set(local(20, 30))
    records = gen.due(queued=0)
    # the hour around the peak (60/s at 19:30, 70/s at 20:00, 57/s at 20:30)
    assert len(records) == int(expected(23, local(19, 30), local(20, 30), MSK))
    assert 62 * 3600 < len(records) < 66 * 3600
    assert {r.msisdn for r in records} == {"13025550100", "13025550101"}
    assert len({r.id for r in records}) == len(records)
    assert all(local(19, 30) < r.event_at <= local(20, 30) for r in records)
    assert [r.event_at for r in records] == sorted(r.event_at for r in records)
    kinds = {k: sum(r.kind == k for r in records) / len(records) for k in ("data", "voice", "sms")}
    assert kinds == pytest.approx({"data": 0.60, "voice": 0.25, "sms": 0.15}, abs=0.01)
    # nothing more until time moves; the fraction left over is carried, not lost
    assert gen.due(queued=0) == []
    clock.set(local(4) + timedelta(days=1))
    night = gen.due(queued=0)
    clock.set(local(4) + timedelta(days=1, seconds=60))
    assert len(gen.due(queued=0)) == pytest.approx(8 * 60, abs=10)
    assert night  # the hours in between were made too, ending at 04:00


@pytest.mark.slow
def test_a_pinned_hour_keeps_that_hours_rate_at_any_time_of_day():
    # the peak hour's 70/s at 04:00, the night's 8/s at 20:00
    assert expected(23, local(4), local(4, 1), MSK, hour=20) == pytest.approx(70 * 60, abs=30)
    assert expected(23, local(20), local(20, 1), MSK, hour=4) == pytest.approx(8 * 60, abs=30)
    clock = FixedClock(local(3))
    gen = UsageLoad(
        23.0, app_env="test", clock=clock, tz=MSK, numbers=lambda _day: ["1"], seed=1, hour=20
    )
    clock.set(local(6))
    assert len(gen.due(queued=0)) == int(expected(23, local(3), local(6), MSK, hour=20))
    assert len(gen.due(queued=0)) == 0
    with pytest.raises(ValueError, match="0-23"):
        UsageLoad(23.0, app_env="test", clock=clock, tz=MSK, numbers=lambda _day: [], hour=24)


def test_a_rate_must_be_positive():
    with pytest.raises(ValueError, match="the usage rate must be positive"):
        load(FixedClock(local(12)), rate=0.0)


def test_nothing_due_without_numbers_or_a_whole_record(caplog):
    clock = FixedClock(local(12))
    tiny = load(clock, rate=0.001)
    clock.set(local(12, 0) + timedelta(seconds=1))
    assert tiny.due(queued=0) == [] and tiny.carry > 0  # the fraction is carried
    nobody = load(clock, numbers=())
    clock.set(local(12, 10))
    caplog.set_level("WARNING", "app.network.usage_load")
    assert nobody.due(queued=0) == []
    assert re.search(r"no assigned MSISDN, \d+ records not made", caplog.text)


def test_a_full_queue_drops_what_does_not_fit_and_counts_it(caplog):
    clock = FixedClock(local(12))
    gen = load(clock, rate=1.0)
    clock.set(local(12, 10))
    count = int(expected(1.0, local(12), local(12, 10), MSK))
    caplog.set_level("WARNING", "app.network.usage_load")
    records = gen.due(queued=MAX_QUEUED - 5)
    assert len(records) == 5 and gen.dropped == count - 5
    assert f"{count - 5} records dropped (queue full)" in caplog.text


def test_the_stub_serves_generated_records_until_acknowledged_and_logs_the_lag(caplog):
    clock = FixedClock(local(12))
    stub = StubNetwork(load(clock, rate=1.0))
    clock.set(local(12, 10))
    caplog.set_level("INFO", "app.network.usage_load")
    first = stub.usage_records(500)
    assert len(first) == 500
    lag = float(re.search(r"lag_seconds=([\d.]+)", caplog.text).group(1))
    assert lag == pytest.approx(600, abs=2)  # the oldest record is from 12:00
    stub.ack_usage([r.id for r in first])
    rest = stub.usage_records(500)
    assert rest and rest[0].id not in {r.id for r in first}
    assert stub.usage_acked == []  # a soak acknowledges millions: they are not kept


@pytest.mark.parametrize("app_env", ["prod", "dev", "staging"])
def test_the_generator_is_refused_outside_test(app_env):
    with pytest.raises(LoadRefused, match="APP_ENV=test"):
        load(FixedClock(local(12)), app_env=app_env)


def test_the_worker_refuses_to_start_with_a_usage_rate_outside_test(monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("BSS_STUB_USAGE_RATE", "23")
    monkeypatch.setattr("app.worker.__main__.settings", Settings())
    assert worker_main(["--once"]) == 2
    assert "BSS_STUB_USAGE_RATE" in capsys.readouterr().err


def test_the_adapter_is_refused_with_a_usage_rate_outside_test():
    # its own patch context: the autouse stub fixture's teardown needs the real adapter
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("APP_ENV", "prod")
        patch.setenv("BSS_STUB_USAGE_RATE", "23")
        patch.setattr(network, "settings", Settings())
        patch.setattr(network, "_ADAPTERS", {})
        with pytest.raises(LoadRefused):
            network.adapter()


def test_the_worker_feed_stores_generated_usage_on_assigned_lines(
    api, db_conn, world, migrated_db_url, monkeypatch
):
    product = line_on(api, db_conn, world.off, world.acc)
    start = api.clock.now()
    clock = FixedClock(start)
    gen = UsageLoad(
        0.5,
        app_env="test",
        clock=clock,
        tz=ZoneInfo(api.clock.tz.key),
        numbers=partial(assigned_numbers, migrated_db_url),
        seed=2,
    )
    monkeypatch.setattr(network, "_ADAPTERS", {"stub": StubNetwork(gen)})
    clock.set(start + timedelta(minutes=10))
    tick(db_conn, clock.now())
    rows = db_conn.execute(
        "SELECT msisdn, product_id::text, count(*) FROM usage_record"
        " WHERE network_record_id LIKE 'lt-gen-%' GROUP BY 1, 2"
    ).fetchall()
    assert gen.made > 0
    assert rows == [(MSISDN, product["id"], gen.made)]
    assert start.astimezone(UTC) < clock.now()
