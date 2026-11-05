"""mcp-oauth-smoke.py against a stub connector: the skip, the checks, the clean-up, no secrets."""

import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).with_name("mcp-oauth-smoke.py")
PASSWORD = "smoke-password-never-printed"  # noqa: S105 - a stub fixture
UI_PASSWORD = "ui-password-never-printed"  # noqa: S105 - a stub fixture


class Stub(BaseHTTPRequestHandler):
    """Just enough of bss_mcp: the users, scopes and refusals the smoke expects."""

    def log_message(self, *args):
        pass

    def answer(self, status, body="", headers=None):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def form(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode()
        if self.headers.get("Content-Type", "").startswith("application/json"):
            return json.loads(raw)
        return {key: values[0] for key, values in parse_qs(raw).items()}

    def do_GET(self):
        url = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        if url.path == "/authorize":
            self.server.states["request"] = query
            base = f"http://127.0.0.1:{self.server.server_port}"
            self.answer(302, headers={"Location": f"{base}/login?request=req-1"})
        else:
            self.answer(404)

    def do_POST(self):
        state, path, body = self.server.states, urlsplit(self.path).path, self.form()
        state.setdefault("calls", []).append(path)
        if path == "/register":
            self.answer(201, {"client_id": "client-1"})
        elif path == "/login":
            if (body["user"], body["password"]) != ("mcp-smoke", PASSWORD):
                self.answer(401, "<p>Wrong user name or password.</p>")
                return
            request = state["request"]
            location = f"{request['redirect_uri']}?code=code-1&state={request['state']}"
            self.answer(302, headers={"Location": location})
        elif path == "/token":
            n = state["issued"] = state.get("issued", 0) + 1
            pair = {"access_token": f"at-{n}", "refresh_token": f"rt-{n}"}
            self.answer(200, {**pair, "scope": body.get("scope", "read")})
        elif path == "/revoke":
            state["revoked"] = body["token"]
            self.answer(200)
        elif path == "/mcp":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if state.get("revoked") or not token:
                self.answer(401)
            elif body["method"] == "tools/list":
                self.answer(200, {"result": {"tools": []}})
            elif body["params"]["name"] == "get_context":
                self.answer(200, {"result": {"isError": False, "content": []}})
            else:  # a write tool
                self.answer(state.get("write_status", 403))
        else:
            self.answer(404)


class OAuthSmokeTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        self.server.states = {}
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def run_smoke(self, mode="local", **env):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("MCP_SMOKE_")}
        return subprocess.run(  # noqa: S603 - the script under test, fixed arguments
            [sys.executable, "-I", str(SCRIPT), self.base, mode],
            env={**clean, **env},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def users(self, ui=True):
        env = {"MCP_SMOKE_USER": "mcp-smoke", "MCP_SMOKE_PASSWORD": PASSWORD}
        if ui:
            env |= {"MCP_SMOKE_UI_USER": "ui-smoke", "MCP_SMOKE_UI_PASSWORD": UI_PASSWORD}
        return env

    def assert_no_secrets(self, result):
        for secret in (PASSWORD, UI_PASSWORD, "at-1", "rt-1", "at-2", "rt-2"):
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_without_a_smoke_user_it_says_skipped_and_succeeds(self):
        result = self.run_smoke("caddy")
        self.assertEqual(result.returncode, 0)
        self.assertIn("mcp oauth smoke: SKIPPED", result.stdout)
        self.assertEqual(self.server.states, {})  # nothing contacted

    def test_the_whole_flow_passes_and_revokes_its_grant(self):
        result = self.run_smoke(**self.users())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("all checks passed", result.stdout)
        self.assertIn("UI user (right password) -> 401", result.stdout)
        self.assertEqual(self.server.states["revoked"], "rt-2")  # the rotated refresh token
        self.assertEqual(self.server.states["request"]["scope"], "read write")
        self.assert_no_secrets(result)

    def test_a_write_tool_not_refused_fails_and_still_revokes(self):
        self.server.states["write_status"] = 200
        result = self.run_smoke(**self.users())
        self.assertEqual(result.returncode, 1)
        self.assertIn("expected 403", result.stderr)
        self.assertEqual(self.server.states["revoked"], "rt-1")
        self.assert_no_secrets(result)

    def test_a_refused_smoke_user_fails_without_printing_the_password(self):
        env = self.users() | {"MCP_SMOKE_PASSWORD": "wrong-" + PASSWORD}
        result = self.run_smoke(**env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("POST /login as the smoke user answered 401", result.stderr)
        self.assertNotIn("wrong-" + PASSWORD, result.stdout + result.stderr)

    def test_local_mode_requires_the_ui_user_and_caddy_mode_says_not_checked(self):
        local = self.run_smoke(**self.users(ui=False))
        self.assertEqual(local.returncode, 1)
        self.assertIn("needs MCP_SMOKE_UI_USER", local.stderr)
        self.server.states.clear()
        caddy = self.run_smoke("caddy", **self.users(ui=False))
        self.assertEqual(caddy.returncode, 0, caddy.stderr)
        self.assertIn("UI-user refusal not checked here", caddy.stdout)


if __name__ == "__main__":
    unittest.main()
