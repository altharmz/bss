"""The worker's clock override (Q14): honoured only when APP_ENV is explicitly "test"."""

from datetime import datetime

import pytest

from app.clock import FixedClock, SystemClock
from app.config import Settings
from app.worker.runtime import FAKE_NOW, StartRefused, clock_from_environment, worker_clock

PINNED = "2026-10-01T00:30:00+03:00"


def test_app_env_unset_means_prod(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert Settings().app_env == "prod"


@pytest.mark.parametrize(
    "app_env", [None, "dev", "prod", "staging"], ids=["unset", "dev", "prod", "staging"]
)
def test_the_override_is_refused_outside_test(monkeypatch, app_env):
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv(FAKE_NOW, PINNED)
    with pytest.raises(StartRefused, match="tests only"):
        clock_from_environment()


def test_the_override_is_honoured_under_test(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(FAKE_NOW, PINNED)
    clock = clock_from_environment()
    assert isinstance(clock, FixedClock)
    assert clock.now() == datetime.fromisoformat(PINNED)


@pytest.mark.parametrize("app_env", [None, "dev", "test"], ids=["unset", "dev", "test"])
def test_without_the_override_the_clock_is_the_system_clock(monkeypatch, app_env):
    if app_env is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.delenv(FAKE_NOW, raising=False)
    assert isinstance(clock_from_environment(), SystemClock)


@pytest.mark.parametrize(
    ("value", "reason"),
    [("tomorrow", "not an ISO 8601 instant"), ("2026-10-01T00:30:00", "needs an offset")],
)
def test_a_malformed_override_is_refused_even_under_test(value, reason):
    with pytest.raises(StartRefused, match=reason):
        worker_clock("test", value)
