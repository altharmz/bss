"""The bound on requests in flight per process (issue #341, app/overload.py)."""

import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import metrics, overload
from app.config import Settings, settings
from app.main import app

pytestmark = pytest.mark.smoke


def scope(path: str = "/tmf-api/customerManagement/v5/customer") -> dict:
    return {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}


class Held:
    """An endpoint double: answers 200 once `release` is set; `entered` counts arrivals."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.entered = 0

    async def __call__(self, scope, receive, send) -> None:
        self.entered += 1
        await self.release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


async def call(middleware, path: str = scope()["path"]) -> list[dict]:
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await middleware(scope(path), receive, send)
    return sent


def status_of(sent: list[dict]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def rejected() -> float:
    return metrics.REJECTED_OVERLOAD._value.get()


@pytest.fixture
def limit_two(monkeypatch):
    monkeypatch.setattr(settings, "bss_max_in_flight", 2)


def test_a_request_beyond_the_limit_gets_503_at_once(limit_two):
    async def run() -> None:
        inner = Held()
        middleware = overload.InFlightLimitMiddleware(inner, overload.InFlightState())
        held = [asyncio.create_task(call(middleware)) for _ in range(2)]
        while inner.entered < 2:
            await asyncio.sleep(0)
        before = rejected()

        sent = await asyncio.wait_for(call(middleware), timeout=1)

        assert inner.entered == 2  # the third never reached the endpoint
        assert status_of(sent) == 503
        start = sent[0]
        assert (b"retry-after", b"1") in start["headers"]
        assert json.loads(sent[1]["body"]) == {
            "@type": "Error",
            "code": "overloaded",
            "reason": "Service Unavailable",
            "message": "the server is at capacity; retry shortly",
            "status": "503",
        }
        assert rejected() == before + 1
        # exempt paths pass the full process
        health = asyncio.create_task(call(middleware, "/healthz"))
        while inner.entered < 3:
            await asyncio.sleep(0)
        inner.release.set()
        assert [status_of(s) for s in await asyncio.gather(*held, health)] == [200, 200, 200]

    asyncio.run(run())


def test_the_count_is_released_on_success_error_and_disconnect(limit_two):
    async def run() -> None:
        state = overload.InFlightState()

        async def fails(scope, receive, send) -> None:
            raise RuntimeError("handler broke")

        with pytest.raises(RuntimeError):
            await call(overload.InFlightLimitMiddleware(fails, state))
        assert state.count == 0

        inner = Held()
        middleware = overload.InFlightLimitMiddleware(inner, state)
        gone = [asyncio.create_task(call(middleware)) for _ in range(2)]
        while inner.entered < 2:
            await asyncio.sleep(0)
        assert state.count == 2
        for task in gone:  # the client went away: the server cancels the request's task
            task.cancel()
        await asyncio.gather(*gone, return_exceptions=True)
        assert state.count == 0

        inner.release.set()
        results = await asyncio.gather(*(call(middleware) for _ in range(2)))
        assert [status_of(s) for s in results] == [200, 200]
        assert state.count == 0

    asyncio.run(run())


def test_the_app_rejects_at_the_limit_and_keeps_healthz_and_metrics(monkeypatch):
    """Registered in app.main inside the request log: the 503 carries X-Request-Id."""
    monkeypatch.setattr(settings, "bss_max_in_flight", 1)
    monkeypatch.setattr(overload.STATE, "count", 1)  # one request already in flight
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.headers["X-Request-Id"]
    assert response.json()["code"] == "overloaded"
    with patch("app.db.ping", return_value=True):
        assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 404  # off without a token, never 503


def test_the_default_is_twice_the_pool_and_zero_is_refused(monkeypatch):
    monkeypatch.delenv("BSS_MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    assert Settings().bss_max_in_flight == 14
    monkeypatch.setenv("BSS_MAX_IN_FLIGHT", "5")
    assert Settings().bss_max_in_flight == 5
    monkeypatch.setenv("BSS_MAX_IN_FLIGHT", "0")
    with pytest.raises(ValidationError):
        Settings()
