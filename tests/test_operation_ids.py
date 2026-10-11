"""operationId = the handler's function name (app/main.py). The MCP connector names its
tools after it, and MCP clients accept tool names of at most 64 characters of this shape."""

import re

from app.main import app

NAME = re.compile(r"^[a-z0-9_]+$")


def operation_ids() -> list[str]:
    return [op["operationId"] for ops in app.openapi()["paths"].values() for op in ops.values()]


def test_operation_ids_are_unique_short_snake_case_names():
    ids = operation_ids()
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)
    for operation_id in ids:
        assert NAME.match(operation_id), operation_id
        assert len(operation_id) <= 64, operation_id


def test_operation_id_is_the_function_name():
    # not FastAPI's default name + path + method (create_payment_tmf_api_..._post)
    assert "create_payment" in operation_ids()
    assert "healthz" in operation_ids()
