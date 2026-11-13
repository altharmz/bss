"""The network's usage feed (docs/decisions/usage.md).

Each tick the unacknowledged records are stored (once each, by the network's record id) in
batches: one transaction per batch, matched to their lines and checked against the
allowances; only after the commit is the batch acknowledged, so a crash in between just
resends it and the id makes the resend harmless. A tick takes batches until the feed is
empty (a batch that is not full) or FEED_BUDGET has passed, so a backlog is worked off
without holding up the rest of the wake-up for longer than that (#281).
"""

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.clock import Clock
from app.integrations.network import adapter
from app.network.usage import Incoming, ingest_batch
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")

BATCH = 500
FEED_BUDGET = timedelta(seconds=120)


def process_usage(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
    budget: timedelta | None = None,
) -> int:
    """Store the feed's waiting records, batch by batch; returns how many were new."""
    ensure_ownership(conn)
    budget = FEED_BUDGET if budget is None else budget
    started = clock.now()
    created = 0
    while True:
        if checkpoint is not None and checkpoint():
            return created
        try:
            records = adapter().usage_records(BATCH)
        except Exception:  # noqa: BLE001 - the network is unreachable: try on the next tick
            log.exception("usage feed")
            return created
        if not records:
            return created
        new, acknowledged = _store(conn, records, clock=clock, tz=tz)
        created += new
        if heartbeat is not None:
            heartbeat()
        # not acknowledged: the same batch would come again at once; leave it to the next tick
        if not acknowledged or len(records) < BATCH or clock.now() - started >= budget:
            return created


def _store(
    conn: psycopg.Connection, records: list, *, clock: Clock, tz: ZoneInfo
) -> tuple[int, bool]:
    """One batch in one transaction, then its acknowledgement: (new records, acknowledged)."""
    with conn.transaction():
        created = ingest_batch(
            conn,
            [
                Incoming(
                    network_record_id=record.id,
                    kind=record.kind,
                    quantity=record.quantity,
                    event_at=record.event_at,
                    msisdn=record.msisdn,
                    imsi=record.imsi,
                    roaming=record.roaming,
                    throttled=record.throttled,
                )
                for record in records
            ],
            now=clock.now(),
            tz=tz,
        )
    try:
        adapter().ack_usage([record.id for record in records])
    except Exception:  # noqa: BLE001 - not acknowledged: resent, stored once
        log.exception("usage acknowledgement")
        return created, False
    return created, True
