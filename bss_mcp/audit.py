"""One JSON line per tool call and per auth event: time, principal, tool or event,
outcome. Never bodies, arguments, tokens or passwords - only the fields named here."""

import json
import logging
from datetime import UTC, datetime

from bss_mcp import metrics
from bss_mcp.clock import Clock

log = logging.getLogger("bss_mcp.audit")


def audit(
    clock: Clock,
    *,
    event: str,
    outcome: str | int,
    principal: str | None = None,
    tool: str | None = None,
    client_id: str | None = None,
    ip: str | None = None,
    seconds: float | None = None,
) -> None:
    line = {
        "time": datetime.fromtimestamp(clock.now(), UTC).isoformat().replace("+00:00", "Z"),
        "event": event,
        "principal": principal,
        "tool": tool,
        "client_id": client_id,
        "ip": ip,
        "outcome": outcome,
    }
    # the counters are fed here, with the line, so the two cannot disagree; `seconds` is a
    # duration for the histogram only, never part of the line
    metrics.record(event, outcome, tool, seconds)
    log.info(json.dumps({k: v for k, v in line.items() if v is not None}, sort_keys=True))
