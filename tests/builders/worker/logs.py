"""The worker's log lines a test captured (#511): caplog holds every logger's records, the
API's request lines too, so a test reads only the worker's."""

import logging


def worker_lines(caplog, level: int = logging.ERROR) -> list[str]:
    """The messages the `app.worker` logger wrote at `level` or above."""
    return [r.getMessage() for r in caplog.records if r.name == "app.worker" and r.levelno >= level]
