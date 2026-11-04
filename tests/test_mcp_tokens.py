"""The MCP connector, slice 5 (docs/mcp.md): the token tooling, `python -m bss_mcp.tokens`,
against the store the running connector reads - a token it creates works at once, a
revocation takes effect on the next request."""

import io
import logging

import pytest

from bss_mcp.tokens import run
from tests import test_mcp_oauth as flow


def tool(mcp, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run(list(argv), mcp.store, mcp.clock, out, err)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def users(db_conn, mcp):
    for name, password in flow.USERS.items():
        flow.add_user(db_conn, name, password)
    return mcp


def test_create_prints_the_token_once_and_it_works_with_its_scope(mcp, caplog):
    caplog.set_level(logging.INFO, logger="bss_mcp.audit")
    code, out, err = tool(mcp, "create", "ci-reader", "--scope", "read")
    assert code == 0
    token = out.strip()
    assert token.startswith("bss_st_") and "\n" not in token
    assert "shown this once" in err and token not in err
    # the token is in no log line, only the event
    assert token not in caplog.text and '"event": "token_create"' in caplog.text
    tools = mcp.tools(token)
    assert "list_customers" in tools and "create_individual" not in tools
    response = mcp.call(token, "create_individual", flow.GIVEN)
    assert response.status_code == 403


def test_list_shows_tokens_without_their_values_and_revoke_ends_them(mcp):
    _, token, _ = tool(mcp, "create", "ci", "--scope", "write")
    token = token.strip()
    (record,) = mcp.store.static_tokens()
    code, out, _ = tool(mcp, "list")
    assert code == 0
    assert record.id in out and "ci" in out and "write" in out
    assert token not in out and token[7:20] not in out
    assert mcp.rpc("tools/list", None, token).status_code == 200

    code, out, _ = tool(mcp, "revoke", record.id)
    assert (code, out.strip()) == (0, f"revoked static token {record.id}")
    assert mcp.rpc("tools/list", None, token).status_code == 401
    # once, and unknown ids are refused
    assert tool(mcp, "revoke", record.id)[0] == 1
    assert tool(mcp, "revoke", "0000000000000000")[0] == 1
    _, out, _ = tool(mcp, "list")
    assert "revoked" in out and out.count(record.id) == 1


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["create", "Upper", "--scope", "read"], "token name must match"),
        (["create", "mcp:x", "--scope", "read"], "token name must match"),
    ],
)
def test_create_refuses_bad_names(mcp, argv, message):
    code, out, err = tool(mcp, *argv)
    assert code == 1 and out == "" and message in err
    assert mcp.store.static_tokens() == []


def test_create_refuses_a_duplicate_name_and_an_unknown_scope(mcp):
    assert tool(mcp, "create", "ci", "--scope", "read")[0] == 0
    code, out, err = tool(mcp, "create", "ci", "--scope", "write")
    assert code == 1 and out == "" and "already exists" in err
    with pytest.raises(SystemExit):
        tool(mcp, "create", "other", "--scope", "admin")
    assert [t.name for t in mcp.store.static_tokens()] == ["ci"]


def test_list_shows_live_oauth_grants_and_revoke_grant_ends_the_login(users):
    issued = flow.tokens(users, scope="read")
    access, refresh = issued["access_token"], issued["refresh_token"]
    assert users.rpc("tools/list", None, access).status_code == 200
    (grant,) = users.store.grants()

    _, out, _ = tool(users, "list")
    assert grant["grant_id"] in out and "demo" in out and "Test client" in out
    assert access not in out and refresh not in out

    code, out, _ = tool(users, "revoke-grant", grant["grant_id"])
    assert code == 0 and "2 live tokens" in out
    assert users.rpc("tools/list", None, access).status_code == 401
    refreshed = flow.token(
        users, issued["client_id"], grant_type="refresh_token", refresh_token=refresh
    )
    assert refreshed.status_code == 400
    assert tool(users, "revoke-grant", grant["grant_id"])[0] == 1
    _, out, _ = tool(users, "list")
    assert grant["grant_id"] not in out
