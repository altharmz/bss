from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_reports_service_status():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_healthz_ok_when_db_reachable():
    with patch("app.db.ping", return_value=True):
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["db"] == "up"


def test_healthz_degraded_when_db_down():
    with patch("app.db.ping", side_effect=RuntimeError("no route to db")):
        resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["db"] == "down"
