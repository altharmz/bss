"""The billing worker process: `python -m app.worker` (a loop) or `--once` (one wake-up).

Refuses to start (exit 2) when BILLING_RUN_AT is not strictly between 00:00 and 02:00
(D-W13), or WORKER_FAKE_NOW or BSS_STUB_USAGE_RATE is set outside APP_ENV=test (Q14,
#281). Wakes at least every
WAKE_SECONDS; SIGTERM (what `docker compose stop` / `up` send) and SIGINT finish the
account in hand, leave the run running for the next worker, close the connection (which
releases the ownership lock) and exit 0 (D-W11). The liveness file is touched on every
wake-up and after every account of every path - retries included, through the heartbeat -
for the container healthcheck (slice f: unhealthy when older than 3 minutes).
"""

import argparse
import logging
import os
import signal
import sys
import threading
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from app import logsetup
from app.config import settings
from app.integrations.network.usage_load import LoadRefused, check_allowed
from app.worker import db
from app.worker.loop import tick
from app.worker.runtime import StartRefused, check_run_at, clock_from_environment

WAKE_SECONDS = 60
LIVENESS = "WORKER_LIVENESS_FILE"

log = logging.getLogger("app.worker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker")
    parser.add_argument("--once", action="store_true", help="one wake-up, then exit")
    args = parser.parse_args(argv)
    logsetup.configure("worker")
    try:
        run_at = check_run_at(settings.billing_run_at)
        clock = clock_from_environment()
        check_allowed(settings.app_env, settings.stub_usage_rate)
    except (StartRefused, LoadRefused) as refused:
        print(f"worker: refusing to start: {refused}", file=sys.stderr)
        return 2
    tz = ZoneInfo(settings.billing_tz)
    liveness = Path(os.environ.get(LIVENESS, "/tmp/worker-alive"))  # noqa: S108 - tmpfs in the container

    stop = threading.Event()

    def on_signal(signum: int, _frame: object) -> None:
        log.info("signal %s: finishing the account in hand, then stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    def checkpoint() -> bool:
        liveness.touch()
        return stop.is_set()

    conn: psycopg.Connection | None = None
    failed = False
    # independent of the tick: a long billing run still sees a log level change (#274)
    poller = logsetup.start_db_poller(clock.now)
    try:
        while True:
            logsetup.request_id.set(f"wake-{uuid.uuid4().hex[:8]}")  # one tick, one ID
            try:
                if conn is None or conn.closed:
                    conn = db.connect()
                result = tick(
                    conn,
                    clock=clock,
                    tz=tz,
                    run_at=run_at,
                    checkpoint=checkpoint,
                    heartbeat=liveness.touch,
                )
                liveness.touch()
                log.info("wake-up: %s", result)
                failed = False
            except psycopg.OperationalError:
                log.exception("database unreachable; trying again at the next wake-up")
                failed = True
                if conn is not None:
                    conn.close()
                conn = None
            except Exception:
                log.exception("wake-up failed")
                failed = True
            if args.once or stop.is_set() or stop.wait(WAKE_SECONDS):
                break
    finally:
        if poller is not None:
            poller.stop()
        if conn is not None:
            conn.close()  # releases the ownership lock
    return 1 if args.once and failed else 0


if __name__ == "__main__":
    sys.exit(main())
