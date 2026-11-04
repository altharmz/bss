"""TMF630 error bodies. Every non-2xx response of the service has this shape - including
FastAPI's own validation errors, unknown routes and unhandled exceptions."""

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.tmfjson import TmfJSONResponse

log = logging.getLogger("app.api")


class TmfError(Exception):
    def __init__(
        self,
        status: int,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        code: str | None = None,
    ):
        super().__init__(message)
        self.code = code  # machine-readable; replaces the status in the body's `code`
        self.status = status
        self.message = message
        self.headers = headers


def bad_request(message: str) -> TmfError:
    return TmfError(400, message)


def not_found(resource: str, resource_id: str) -> TmfError:
    return TmfError(404, f"{resource} {resource_id} not found")


def conflict(message: str) -> TmfError:
    return TmfError(409, message)


def unprocessable(message: str) -> TmfError:
    return TmfError(422, message)


def error_response(
    status: int, message: str, headers: dict[str, str] | None = None, code: str | None = None
) -> TmfJSONResponse:
    body = {
        "@type": "Error",
        "code": code or str(status),
        "reason": HTTPStatus(status).phrase,
        "message": message,
        "status": str(status),
    }
    return TmfJSONResponse(body, status_code=status, headers=headers)


def describe_validation(errors: list[dict]) -> tuple[int, str]:
    """Status and message for Pydantic errors: an unknown attribute is a 400, anything
    else (missing, wrong type, bad value) a 422."""
    return _describe(validation_problems(errors))


# (where, message, unknown): one problem of a request body
Problem = tuple[str, str, bool]


def validation_problems(errors: list[dict]) -> list[Problem]:
    problems = []
    for err in errors:
        loc = ".".join(str(p) for p in err["loc"] if p != "body")
        if err["type"] == "extra_forbidden":
            problems.append((loc, "unknown attribute", True))
        else:
            problems.append((loc, err["msg"], False))
    return problems


def _describe(problems: list[Problem]) -> tuple[int, str]:
    status = 400 if all(unknown for _, _, unknown in problems) else 422
    return status, "; ".join(f"{loc}: {msg}" if loc else msg for loc, msg, _ in problems)


def problems_error(problems: list[Problem]) -> TmfError:
    """One error for every problem: 400 when all are unknown attributes, else 422."""
    return TmfError(*_describe(problems))


def validation_error(exc: ValidationError) -> TmfError:
    return problems_error(validation_problems(exc.errors(include_url=False, include_context=False)))


def install(app: FastAPI) -> None:
    @app.exception_handler(TmfError)
    def _tmf(request: Request, exc: TmfError) -> TmfJSONResponse:
        return error_response(exc.status, exc.message, exc.headers, exc.code)

    @app.exception_handler(StarletteHTTPException)
    def _http(request: Request, exc: StarletteHTTPException) -> TmfJSONResponse:
        if exc.status_code == 404:
            message = f"no resource at {request.url.path}"
        elif exc.status_code == 405:
            message = f"{request.method} is not supported on {request.url.path}"
        else:
            message = str(exc.detail)
        return error_response(exc.status_code, message, dict(exc.headers or {}))

    @app.exception_handler(RequestValidationError)
    def _request_validation(request: Request, exc: RequestValidationError) -> TmfJSONResponse:
        # path and query parameters FastAPI parsed itself; bodies are validated in handlers
        parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return error_response(400, "; ".join(parts))

    @app.exception_handler(Exception)
    def _unhandled(request: Request, exc: Exception) -> TmfJSONResponse:
        # exc_info explicitly: Starlette calls a sync handler in a threadpool thread, where
        # sys.exc_info() is empty and log.exception() alone logs "NoneType: None"
        log.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        return error_response(500, "internal error")
