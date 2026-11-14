"""Scrape lt-app's /metrics into a file every INTERVAL seconds (#281, docs/loadtest.md).

Runs in the lt-scrape container (the k6 cpuset) for the length of a scenario, started and
stopped by loadtest/run.py: `python -m loadtest.scrape <run dir>`. Each scrape is appended
as `# scrape <UTC time>` followed by the exposition text; a failed scrape is one
`# scrape <time> error <reason>` line. The bearer token comes from LOADTEST_METRICS_TOKEN.
"""

import os
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

URL = os.environ.get("LOADTEST_METRICS_URL", "http://lt-app:8080/metrics")
INTERVAL = 15.0


def main(argv: list[str]) -> int:
    out = Path("/app/loadtest/out") / argv[0] / "metrics.prom"
    token = os.environ.get("LOADTEST_METRICS_TOKEN", "")
    request = urllib.request.Request(URL, headers={"Authorization": f"Bearer {token}"})  # noqa: S310 - fixed http URL on the lab network
    while True:
        started = time.monotonic()
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - as above
                text = response.read().decode()
            chunk = f"# scrape {stamp}\n{text}"
        except Exception as error:  # noqa: BLE001 - recorded, the next scrape tries again
            chunk = f"# scrape {stamp} error {error!r}\n"
        with out.open("a") as f:
            f.write(chunk)
        time.sleep(max(0.0, INTERVAL - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
