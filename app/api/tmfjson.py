"""JSON in and out without ever touching float.

In: numbers with a fraction or exponent are parsed straight into Decimal, so an amount
like 19.99 is never a binary float on its way to the database.

Out: the standard library and Pydantic can only emit a Decimal as a string or via float.
TMF Money.value is a JSON number, so this module writes Decimals as number tokens itself
and refuses floats outright - a float reaching the encoder is a bug, not a rounding choice.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import Response


class InvalidJSON(ValueError):
    pass


def _reject_constant(name: str) -> Any:
    raise InvalidJSON(f"{name} is not valid JSON")


def loads(body: bytes) -> Any:
    try:
        return json.loads(body, parse_float=Decimal, parse_constant=_reject_constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidJSON(str(exc)) from exc


def _encode(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError(f"cannot encode non-finite Decimal {value}")
        out.append(format(value, "f"))
    elif isinstance(value, float):
        raise TypeError("float must not reach the JSON encoder - use Decimal")
    elif isinstance(value, dict):
        out.append("{")
        for i, (key, item) in enumerate(value.items()):
            if i:
                out.append(",")
            out.append(json.dumps(str(key), ensure_ascii=False))
            out.append(":")
            _encode(item, out)
        out.append("}")
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, out)
        out.append("]")
    elif isinstance(value, datetime):
        out.append(json.dumps(value.isoformat().replace("+00:00", "Z")))
    elif isinstance(value, (UUID, date)):
        out.append(json.dumps(str(value)))
    else:
        raise TypeError(f"cannot encode {type(value).__name__} as JSON")


def dumps(value: Any) -> str:
    out: list[str] = []
    _encode(value, out)
    return "".join(out)


class TmfJSONResponse(Response):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return dumps(content).encode("utf-8")
