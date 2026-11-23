"""Shared builders of tests/test_prepaid_command_api.py, used by other test modules (#423)."""

from app.repository import prepaid_commands as commands
from tests.builders.prepaid_dispatch import dispatch
from tests.builders.prepaid_posting import prepared


def failed(db_conn, monkeypatch):
    """A real postable command whose first attempt failed technically, effects rolled back."""
    acc, prod, cmd, _ = prepared(db_conn)
    db_conn.commit()
    original = commands.complete

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after completion")

    monkeypatch.setattr(commands, "complete", crash)
    assert dispatch(db_conn) == [cmd["id"]]
    monkeypatch.setattr(commands, "complete", original)
    db_conn.commit()
    return acc, prod, cmd
