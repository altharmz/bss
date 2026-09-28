import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.clock import FixedClock, SystemClock

APP_DIR = Path(__file__).resolve().parent.parent / "app"
FORBIDDEN = re.compile(r"\bdatetime\.(now|utcnow|today)\s*\(|\bdate\.today\s*\(|\btime\.time\s*\(")


def test_only_clock_module_reads_the_wall_clock():
    files = sorted(APP_DIR.rglob("*.py"))
    assert len(files) > 5, "the scan found suspiciously few files"
    offenders = [
        f"{path.relative_to(APP_DIR.parent)}:{n}: {line.strip()}"
        for path in files
        if path.name != "clock.py" or path.parent != APP_DIR
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if FORBIDDEN.search(line)
    ]
    assert offenders == [], "read the time via app.clock instead:\n" + "\n".join(offenders)
    # the pattern must actually match what clock.py does, or this test checks nothing
    assert FORBIDDEN.search((APP_DIR / "clock.py").read_text())


def test_today_is_the_date_in_billing_timezone():
    # 22:30 UTC on Jan 31 is already Feb 1 in Moscow (UTC+3)
    clock = FixedClock(datetime(2026, 1, 31, 22, 30, tzinfo=UTC), tz="Europe/Moscow")
    assert clock.today() == date(2026, 2, 1)
    assert FixedClock(clock.now(), tz="UTC").today() == date(2026, 1, 31)


def test_fixed_clock_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 1, 1))


def test_system_clock_is_timezone_aware():
    assert SystemClock().now().tzinfo is not None
