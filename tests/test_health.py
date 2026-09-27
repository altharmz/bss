from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_without_session_redirects_to_login():
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


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
