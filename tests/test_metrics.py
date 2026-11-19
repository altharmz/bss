"""GET /metrics: token access, HTTP series, database series (issue #240)."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import metrics
from app.config import settings
from app.main import app
from app.repository import network_commands
from tests import factories

TOKEN = "scrape-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FAMILIES = (
    "bss_build_info",
    "bss_http_requests_total",
    "bss_http_request_duration_seconds",
    "bss_http_requests_in_progress",
    "bss_http_in_flight",
    "bss_http_rejected_overload_total",
    "bss_db_up",
    "bss_billing_last_run_status",
    "bss_billing_last_run_age_seconds",
    "bss_queue_pending",
    "bss_queue_oldest_pending_age_seconds",
    "bss_queue_failed",
    "bss_products",
    "bss_usage_record_default_rows",
)


@pytest.fixture
def client(api, db_conn, monkeypatch):
    monkeypatch.setattr(settings, "bss_metrics_token", TOKEN)
    metrics.DB_COLLECTOR._at = None  # the 30 s cache is process-wide
    with TestClient(app, raise_server_exceptions=False) as raw:
        raw.clock = api.clock
        yield raw
    metrics.DB_COLLECTOR._at = None


def sample(text: str, name: str, **labels) -> float | None:
    for line in text.splitlines():
        if line.startswith(name + "{") or line.startswith(name + " "):
            if all(f'{k}="{v}"' in line for k, v in labels.items()):
                return float(line.rsplit(" ", 1)[1])
    return None


def test_disabled_without_token_is_404(client, monkeypatch):
    monkeypatch.setattr(settings, "bss_metrics_token", "")
    assert client.get("/metrics", headers=AUTH).status_code == 404


def test_wrong_or_missing_bearer_is_401(client):
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": TOKEN}).status_code == 401


def test_right_bearer_exposes_every_family(client, db_conn):
    factories.run(db_conn)
    db_conn.commit()
    response = client.get("/metrics", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    for family in FAMILIES:
        assert f"# TYPE {family} " in response.text, family
    assert sample(response.text, "bss_db_up") == 1
    statuses = ("ok", "running", "stale", "failed", "none")
    assert [sample(response.text, "bss_billing_last_run_status", status=s) for s in statuses] == [
        0,
        1,
        0,
        0,
        0,
    ]  # the factory's run is still running


def test_requests_count_the_route_template_not_the_raw_path(client):
    client.get("/tmf-api/customer/v5/customer/00000000-0000-0000-0000-000000000001")
    client.get("/no/such/path")
    client.get("/healthz")
    text = client.get("/metrics", headers=AUTH).text
    assert 'route="/tmf-api/customer/v5/customer/{customer_id}"' in text or (
        'route="/tmf-api/customer/v5/customer/{id}"' in text
    ), text
    assert "00000000-0000-0000-0000-000000000001" not in text
    assert sample(text, "bss_http_requests_total", route="unmatched", status="404") is not None
    assert 'route="/healthz"' not in text and 'route="/metrics"' not in text


def test_metrics_is_not_in_the_openapi_schema():
    assert "/metrics" not in app.openapi()["paths"]


def test_pending_network_command_shows_after_the_cache_expires(client, db_conn):
    before = client.get("/metrics", headers=AUTH).text
    assert sample(before, "bss_queue_pending", queue="network_command") == 0
    world = factories.world(db_conn)
    order = db_conn.execute("SELECT product_order_id, id FROM product_order_item").fetchone()
    network_commands.insert(
        db_conn,
        order_id=order[0],
        order_item_id=order[1],
        product_id=world.product["id"],
        kind="activate",
        due_date=date(2026, 10, 2),
        profile={},
        now=client.clock.now(),
    )
    db_conn.commit()
    cached = client.get("/metrics", headers=AUTH).text
    assert sample(cached, "bss_queue_pending", queue="network_command") == 0
    client.clock.set(client.clock.now() + timedelta(seconds=metrics.CACHE_SECONDS + 1))
    text = client.get("/metrics", headers=AUTH).text
    assert sample(text, "bss_queue_pending", queue="network_command") == 1
    assert sample(text, "bss_queue_oldest_pending_age_seconds", queue="network_command") == 31
    assert sample(text, "bss_queue_failed", queue="network_command") == 0
    assert sample(text, "bss_queue_failed", queue="worker_request") is None


def test_a_usage_record_without_its_months_partition_shows_in_the_default_gauge(client, db_conn):
    """#282: the DEFAULT partition is the safety net of a missing monthly partition."""
    assert sample(client.get("/metrics", headers=AUTH).text, "bss_usage_record_default_rows") == 0
    db_conn.execute(
        "INSERT INTO usage_record (id, network_record_id, kind, quantity, msisdn, event_at,"
        " event_date, status, rejection_reason, created_at)"
        " VALUES (gen_random_uuid(), 'n-1', 'sms', 1, '79990000001', '2040-01-02T12:00Z',"
        " '2040-01-02', 'rejected', 'noLine', now())"
    )
    db_conn.commit()
    client.clock.set(client.clock.now() + timedelta(seconds=metrics.CACHE_SECONDS + 1))
    assert sample(client.get("/metrics", headers=AUTH).text, "bss_usage_record_default_rows") == 1


def test_database_failure_gives_db_up_zero_and_no_other_database_series(client):
    with patch("app.metrics.db.connection", side_effect=RuntimeError("down")):
        text = client.get("/metrics", headers=AUTH).text
    assert sample(text, "bss_db_up") == 0
    assert "bss_queue_pending{" not in text
    assert "bss_products{" not in text
    assert "bss_billing_last_run_status{" not in text


def test_a_scrape_sums_the_counters_of_every_app_process(client):
    """WEB_CONCURRENCY app processes share PROMETHEUS_MULTIPROC_DIR (#288): another process
    counts 3 requests on a route, this one 1, and one scrape shows 4."""
    import os
    import subprocess
    import sys

    route = "/test/another/process"
    other = f"from app import metrics\nmetrics.REQUESTS.labels('GET', {route!r}, '200').inc(3)\n"
    subprocess.run(  # noqa: S603 - a fixed child Python, same environment and directory
        [sys.executable, "-c", other], check=True, env=os.environ.copy()
    )
    metrics.REQUESTS.labels("GET", route, "200").inc()
    text = client.get("/metrics", headers=AUTH).text
    assert sample(text, "bss_http_requests_total", route=route, status="200") == 4


def test_no_per_process_collectors_are_exported(client):
    text = client.get("/metrics", headers=AUTH).text
    assert "process_resident_memory_bytes" not in text and "python_info" not in text
