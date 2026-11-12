"""Guard against measuring the wrong billing date and hiding failed endpoint groups."""

from datetime import date

import pytest

from loadtest.run import billing_date, feed_lag, groups_of


def test_billing_night_requires_exactly_todays_run_to_be_outstanding():
    today = date(2026, 11, 12)
    assert billing_date("2026-11-11", today) == today
    for last in ("", "2026-11-04", "2026-11-12", "2026-11-13"):
        with pytest.raises(ValueError, match="snapshot billed through yesterday"):
            billing_date(last, today)


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


def test_feed_lag_report_retains_the_worst_backlog_even_if_it_recovers():
    report = feed_lag(
        "stub usage load: queued=8000 made=9000 dropped=0 lag_seconds=350.1\n"
        "stub usage load: queued=500 made=9500 dropped=0 lag_seconds=20.0\n"
    )
    assert report["max_lag_seconds"] == 350.1
    assert report["last_lag_seconds"] == 20
    assert report["max_queued"] == 8000
