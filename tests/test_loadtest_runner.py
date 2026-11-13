"""Guard against measuring the wrong billing date and hiding failed endpoint groups."""

from datetime import UTC, date, datetime

import pytest

from loadtest.run import billing_date, feed_lag, feed_of, groups_of, mcp_tokens, settled


def test_billing_night_catches_up_the_first_unbilled_date_before_today():
    today = date(2026, 11, 12)
    assert billing_date("2026-11-04", today) == date(2026, 11, 5)
    assert billing_date("2026-11-10", today) == date(2026, 11, 11)
    # today's own run belongs to the worker's schedule, and a billed-up dataset has no date
    for last in ("", "2026-11-11", "2026-11-12", "2026-11-13"):
        with pytest.raises(ValueError, match="billing-night needs"):
            billing_date(last, today)


def test_the_feed_runs_at_the_peak_hour_except_in_smoke_and_billing_night():
    assert feed_of("smoke") == (0.0, -1)
    assert feed_of("billing-night") == (23.0, 4)
    for scenario in ("steady", "stress", "soak", "prod-shaped"):
        assert feed_of(scenario) == (23.0, 20)


def test_mcp_tokens_keep_every_principal_under_60_calls_a_minute():
    steps = [1.0, 2.0, 3.0, 5.0]
    assert mcp_tokens("smoke", 1, steps) == 3  # 2/s = 120 a minute over 3 tokens
    assert mcp_tokens("stress", 1, steps) == 15  # 10/s at the 5x step
    assert mcp_tokens("billing-night", 1, steps) == 1  # 20 % of the mix: 24 a minute
    for scenario, mult in (("steady", 1), ("steady", 20), ("soak", 2.5)):
        assert 2 * mult * 60 / mcp_tokens(scenario, mult, steps) <= 40


def test_report_does_not_hide_an_http_error_behind_fast_responses():
    summary = {
        "metrics": {
            "http_req_duration{grp:read}": {
                "values": {
                    "count": 1000,
                    "med": 10,
                    "p(95)": 20,
                    "p(99)": 30,
                    "max": 40,
                }
            },
            "http_req_failed{grp:read}": {"values": {"rate": 0.002}},
        }
    }
    row = groups_of(summary, 100)["read"]
    assert row["rps"] == 10
    assert row["pass"] is False


def feed_line(ts, queued, made, lag):
    return (
        f'lt-worker-1  | {{"ts": "{ts}", "level": "INFO", "msg": "stub usage load:'
        f' queued={queued} made={made} dropped=0 lag_seconds={lag}", "process": "worker"}}\n'
    )


LOG = (
    feed_line("2026-11-13T18:05:58.361Z", 39074, 40574, 558.8)
    + feed_line("2026-11-13T18:07:00.000Z", 8000, 45000, 350.1)
    + feed_line("2026-11-13T18:08:00.000Z", 500, 49500, 20.0)
)


def test_feed_lag_report_retains_the_worst_backlog_even_if_it_recovers():
    report = feed_lag(LOG)
    assert report["max_lag_seconds"] == 558.8
    assert report["last_lag_seconds"] == 20
    assert report["max_queued"] == 39074 and report["records_made"] == 49500


def test_feed_lag_counts_only_the_measured_window():
    report = feed_lag(LOG, datetime(2026, 11, 13, 18, 6, 30, tzinfo=UTC))
    assert report["samples"] == 2 and report["max_lag_seconds"] == 350.1
    assert feed_lag("", None)["max_lag_seconds"] is None


def test_the_run_waits_for_a_finished_wake_up_and_a_caught_up_feed():
    woke = 'lt-worker-1  | {"msg": "wake-up: Tick(...)"}\n'
    assert not settled(LOG, feed=True)  # no wake-up finished yet
    assert settled(woke, feed=False)
    assert not settled(woke, feed=True)  # the feed has not been fetched
    assert not settled(woke + feed_line("2026-11-13T18:07:00.000Z", 8000, 45000, 350.1), feed=True)
    assert settled(woke + LOG, feed=True)
