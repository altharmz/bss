"""The bss app over HTTP - the connector's only way to bss (never the database)."""

import logging
from typing import Any

import anyio
import httpx2

from bss_mcp.clock import Clock

log = logging.getLogger("bss_mcp")

# What the app's `/` answers (app/main.py, root). Any other answer is not bss.
IDENTITY = {"service": "bss", "status": "ok"}


class NotBss(RuntimeError):
    """BSS_API_URL answers, but not as bss - or never answered."""


class Bss:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        transport: httpx2.AsyncBaseTransport | None = None,
        *,
        service_secret: str = "",
    ) -> None:
        self.base_url = base_url
        self.service_secret = service_secret
        self.client = httpx2.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def request(
        self,
        method: str,
        path: str,
        *,
        principal: str,
        params: dict[str, str] | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
        credential_version: int | None = None,
    ) -> httpx2.Response:
        # X-Remote-User: who did it, for the app's operator (display and bill history)
        headers = {**(headers or {}), "X-Remote-User": principal, "Accept": "application/json"}
        if self.service_secret:
            headers["X-Bss-Service"] = self.service_secret
        if credential_version is not None:
            headers["X-Bss-Credential-Version"] = str(credential_version)
        if content_type:
            headers["Content-Type"] = content_type
        return await self.client.request(method, path, params=params, content=body, headers=headers)

    async def credential_check(self, username: str, password: str) -> httpx2.Response:
        """The app's answer to "is this an active `mcp` user's password?" (the OAuth login).
        200 {username, role, credentialVersion}; 401 invalidCredentials (also for an
        `admin` or `user`); 429 too many attempts."""
        return await self.client.post(
            "/internal/v1/service/credential-check",
            json={"username": username, "password": password},
            headers={"X-Bss-Service": self.service_secret, "Accept": "application/json"},
        )

    async def identify(self, clock: Clock, wait: float) -> None:
        """Refuse to serve unless BSS_API_URL is bss: `GET /` must answer exactly
        IDENTITY. No answer at all (the app still starting, e.g. after the nightly reboot)
        is retried for `wait` seconds; a wrong answer fails at once."""
        deadline = clock.monotonic() + wait
        while True:
            try:
                response = await self.client.get("/")
            except httpx2.TransportError as exc:
                if clock.monotonic() >= deadline:
                    raise NotBss(f"{self.base_url} did not answer: {exc!r}") from exc
                log.info("identity check: %s not answering yet, retrying", self.base_url)
                await anyio.sleep(1)
                continue
            body: Any
            try:
                body = response.json()
            except ValueError:
                body = None
            if response.status_code != 200 or body != IDENTITY:
                raise NotBss(
                    f"{self.base_url} is not bss: GET / answered {response.status_code}"
                    f" {response.text[:200]!r}"
                )
            return

    async def aclose(self) -> None:
        await self.client.aclose()
