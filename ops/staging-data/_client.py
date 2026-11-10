"""Shared by the staging-data scripts: sign in as the UI does, read and change records
over HTTP, and keep the dry-run rule in one place.

The scripts read `BSS_URL`, `BSS_USER` and `BSS_PASSWORD` from the shell (never from a
file), print every change, and write only with `--apply`. Standard library only, so they
run on the human's machine without the project's environment.
"""

import argparse
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

CATALOG = "/tmf-api/productCatalogManagement/v5"
MERGE_PATCH = "application/merge-patch+json"
PAGE = 100

# (method, path, body, content type) -> (status, headers, parsed body)
Transport = Callable[[str, str, Any, str], tuple[int, dict[str, str], Any]]


class ScriptError(Exception):
    pass


def http_transport(base: str) -> Transport:
    """The real transport: urllib with a cookie jar, as a browser keeps its session."""
    if not base.startswith(("http://", "https://")):
        raise ScriptError(f"BSS_URL must start with http:// or https://, not {base!r}")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def send(method: str, path: str, body: Any, content_type: str):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(  # noqa: S310 - scheme checked above
            base.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Content-Type": content_type, "X-Bss-Request": "1"},
        )
        try:
            with opener.open(request, timeout=30) as response:  # noqa: S310
                status, headers, raw = response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            status, headers, raw = exc.code, dict(exc.headers), exc.read()
        return status, headers, (json.loads(raw) if raw else None)

    return send


class Client:
    def __init__(self, transport: Transport, apply: bool) -> None:
        self.transport = transport
        self.apply = apply
        self.changes = 0

    def login(self, user: str, password: str) -> None:
        status, _, body = self.transport(
            "POST",
            "/internal/v1/session",
            {"username": user, "password": password},
            "application/json",
        )
        if status != 200:
            raise ScriptError(f"sign-in as {user} failed: {status} {body}")

    def get(self, path: str) -> Any:
        status, _, body = self.transport("GET", path, None, "application/json")
        if status != 200:
            raise ScriptError(f"GET {path} -> {status} {body}")
        return body

    def all(self, path: str) -> list[dict[str, Any]]:
        """Every item of a TMF list, page by page."""
        items: list[dict[str, Any]] = []
        joiner = "&" if "?" in path else "?"
        while True:
            status, headers, body = self.transport(
                "GET", f"{path}{joiner}offset={len(items)}&limit={PAGE}", None, "application/json"
            )
            if status != 200:
                raise ScriptError(f"GET {path} -> {status} {body}")
            items += body
            # no total is sent (#312): the last page has no Link rel="next"
            link = {k.lower(): v for k, v in headers.items()}.get("link", "")
            if not body or 'rel="next"' not in link:
                return items

    def write(self, method: str, path: str, body: dict[str, Any], what: str) -> Any:
        """Print the change; send it only with --apply. Returns the answer, or None."""
        print(f"{'APPLY' if self.apply else 'would'}: {what}")
        self.changes += 1
        if not self.apply:
            return None
        content_type = MERGE_PATCH if method == "PATCH" else "application/json"
        status, _, answer = self.transport(method, path, body, content_type)
        if status not in (200, 201):
            raise ScriptError(f"{method} {path} -> {status} {answer}")
        return answer

    def post(self, path: str, body: dict[str, Any], what: str) -> Any:
        return self.write("POST", path, body, what)

    def patch(self, path: str, body: dict[str, Any], what: str) -> Any:
        return self.write("PATCH", path, body, what)


def run(description: str, work: Callable[[Client], None], argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args(argv)
    try:
        base, user, password = (
            os.environ.get(n, "") for n in ("BSS_URL", "BSS_USER", "BSS_PASSWORD")
        )
        if not (base and user and password):
            raise ScriptError("set BSS_URL, BSS_USER and BSS_PASSWORD in your shell")
        client = Client(http_transport(base), args.apply)
        client.login(user, password)
        work(client)
    except ScriptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    mode = "applied" if args.apply else "dry run, nothing written; add --apply to write"
    print(f"{client.changes} change(s) ({mode})")
    return 0
