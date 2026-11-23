"""Shared builders of tests/test_users.py, used by other test modules (#423)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests import contract

SESSION = "/internal/v1/session"

USERS = "/internal/v1/users"

PASSWORD = "test-password-123"


def login(client, username="i.petrova", password=PASSWORD):
    return client.post(SESSION, json={"username": username, "password": password})


@pytest.fixture
def client(api):
    with TestClient(app, headers={"X-Bss-Request": "1"}, raise_server_exceptions=False) as raw:
        yield raw


def create(client, username="alice", role="user"):
    response = client.post(USERS, json={"username": username, "role": role, "password": PASSWORD})
    assert response.status_code == 201, response.text
    assert "password" not in response.text
    contract.assert_valid("INTERNAL", "User", response.json())
    return response.json()


def activate(client, username="alice"):
    assert login(client, username).status_code == 200
    response = client.post(
        SESSION + "/password",
        json={
            "currentPassword": PASSWORD,
            "newPassword": "replacement-password",
        },
    )
    assert response.status_code == 204, response.text
    assert login(client, username, "replacement-password").status_code == 200
