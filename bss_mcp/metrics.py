"""Prometheus metrics of the connector (issue #240; docs/mcp.md, "Metrics").

Fed from `bss_mcp.audit.audit` - the one place every tool call and OAuth event passes - so
the audit log and the counters cannot disagree. `GET /metrics` is off (404) unless
BSS_METRICS_TOKEN is set (the app's variable, the same value), then needs
`Authorization: Bearer <token>`. No tool label on the histogram and a bounded `result` for
OAuth events keep the cardinality fixed: client-chosen text never becomes a label.
"""

import hmac

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from bss_mcp.config import Settings

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
OUTCOMES = (
    "ok",
    "client_error",
    "forbidden",
    "invalid_arguments",
    "timeout",
    "rate_limited",
    "server_error",
)
# the events the audit log records for OAuth (the per-IP limiter names its event after the path)
OAUTH_EVENTS = {"register", "login", "consent", "token", "refresh", "revoke", "authorize"}

REGISTRY = CollectorRegistry()
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
BUILD_INFO = Gauge("bss_build_info", "Build version.", ["version"], registry=REGISTRY)
TOOL_CALLS = Counter(
    "bss_mcp_tool_calls_total",
    'Tool calls by tool and outcome (rate_limited has tool "none").',
    ["tool", "outcome"],
    registry=REGISTRY,
)
TOOL_DURATION = Histogram(
    "bss_mcp_tool_call_duration_seconds",
    "Tool call duration by outcome.",
    ["outcome"],
    buckets=BUCKETS,
    registry=REGISTRY,
)
OAUTH_EVENTS_TOTAL = Counter(
    "bss_mcp_oauth_events_total",
    "OAuth events from the audit log by event and result.",
    ["event", "result"],
    registry=REGISTRY,
)


def set_build_info(version: str) -> None:
    BUILD_INFO.clear()
    BUILD_INFO.labels(version).set(1)


def tool_outcome(outcome: str | int) -> str:
    """The audit line's outcome (the app's status, 403, or a word) as one of OUTCOMES."""
    if isinstance(outcome, int):
        if outcome == 403:
            return "forbidden"
        if outcome == 429:
            return "rate_limited"
        if 200 <= outcome < 400:
            return "ok"
        return "server_error" if outcome >= 500 else "client_error"
    return {
        "invalid_arguments": "invalid_arguments",
        "timeout": "timeout",
        "unreachable": "server_error",
        # a stale sign-in, an unknown tool name: the caller's to fix
        "credential_revoked": "client_error",
        "unknown_tool": "client_error",
    }.get(outcome, "server_error")


def oauth_result(outcome: str | int) -> str:
    """A bounded `result` for the OAuth event's free-text outcome."""
    if outcome == 429:
        return "rate_limited"
    text = str(outcome)
    if text.startswith("ok") or text == "issued":
        return "ok"
    if text.startswith("refused"):
        return "refused"
    if text == "already revoked":
        return "already_revoked"
    if text.endswith("revoked"):
        return "revoked"
    if text in {"failed", "denied"}:
        return text
    if text == "code reused":
        return "code_reused"
    if text in {"bss down", "bss error"}:
        return "error"
    return "other"


def record(event: str, outcome: str | int, tool: str | None, seconds: float | None) -> None:
    if event == "tool_call":
        label = "unknown" if outcome == "unknown_tool" else (tool or "unknown")
        result = tool_outcome(outcome)
        TOOL_CALLS.labels(label, result).inc()
        if seconds is not None:
            TOOL_DURATION.labels(result).observe(seconds)
    elif event == "rate_limited":
        TOOL_CALLS.labels("none", "rate_limited").inc()
    elif event in OAUTH_EVENTS:
        OAUTH_EVENTS_TOTAL.labels(event, oauth_result(outcome)).inc()


def endpoint(settings: Settings):
    async def metrics(request: Request) -> Response:
        token = settings.bss_metrics_token
        if not token:
            return JSONResponse({"error": "not_found"}, status_code=404)
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip(), token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE)

    return metrics
