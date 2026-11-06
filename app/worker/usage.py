"""The network's usage feed (docs/decisions/usage.md).

Each tick the unacknowledged records are stored (once each, by the network's record id) in
one transaction, matched to their lines and checked against the allowances; only after the
commit are they acknowledged, so a crash in between just resends them and the id makes the
resend harmless.
"""

import logging
from zoneinfo import ZoneInfo

import psycopg

from app.clock import Clock
from app.integrations.network import adapter
from app.network.usage import Incoming, ingest
from app.worker.process import Checkpoint, Heartbeat
from app.worker.runs import ensure_ownership

log = logging.getLogger("app.worker")

BATCH = 500


def process_usage(
    conn: psycopg.Connection,
    *,
    clock: Clock,
    tz: ZoneInfo,
    checkpoint: Checkpoint | None = None,
    heartbeat: Heartbeat | None = None,
) -> int:
    """Store one batch of the feed; returns how many records were new."""
    ensure_ownership(conn)
    if checkpoint is not None and checkpoint():
        return 0
    try:
        records = adapter().usage_records(BATCH)
    except Exception:  # noqa: BLE001 - the network is unreachable: try on the next tick
        log.exception("usage feed")
        return 0
    if not records:
        return 0
    now = clock.now()
    created = 0
    with conn.transaction():
        for record in records:
            _, new = ingest(
                conn,
                Incoming(
                    network_record_id=record.id,
                    kind=record.kind,
                    quantity=record.quantity,
                    event_at=record.event_at,
                    msisdn=record.msisdn,
                    imsi=record.imsi,
                    roaming=record.roaming,
                    throttled=record.throttled,
                ),
                now=now,
                tz=tz,
            )
            created += new
    try:
        adapter().ack_usage([record.id for record in records])
    except Exception:  # noqa: BLE001 - not acknowledged: resent, stored once
        log.exception("usage acknowledgement")
    if heartbeat is not None:
        heartbeat()
    return created
