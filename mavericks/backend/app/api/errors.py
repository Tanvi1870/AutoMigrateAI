"""One error shape for every failure: {status, code, message, details}. Internals are never returned."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agents.migration_agent import MigrationBlockedError
from app.agents.repair_agent import RepairNotPossibleError
from app.execution.engine import ExecutionCapacityError
from app.generators.base_generator import GenerationError
from app.models.base import CamelModel
from app.parsers.base_parser import ParserError
from app.qtest.base_adapter import QTestError
from app.services.errors import MigrationStateError
from app.services.migration_service import UnsupportedUploadError
from app.storage.json_store import MigrationStoreError
from app.storage.migration_store import MigrationBusyError, MigrationNotFoundError

log = logging.getLogger(__name__)

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    501: "NOT_IMPLEMENTED",
}


class ErrorDetail(CamelModel):
    field: str | None = None
    message: str


class ErrorResponse(CamelModel):
    status: int
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


# Documented on every API route so clients see the same error contract in OpenAPI.
ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status: {"model": ErrorResponse} for status in (401, 404, 409, 413, 415, 422, 429, 500, 501, 502)
}


def error_response(status: int, code: str, message: str, details: list[ErrorDetail] | None = None) -> JSONResponse:
    body = ErrorResponse(status=status, code=code, message=message, details=details or [])
    return JSONResponse(status_code=status, content=body.model_dump(mode="json", by_alias=True))


def _messages(messages: list[str]) -> list[ErrorDetail]:
    return [ErrorDetail(message=message) for message in messages]


async def _validation(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [
        ErrorDetail(
            field=".".join(str(part) for part in error["loc"] if part not in ("body", "query", "path")) or None,
            message=error["msg"],
        )
        for error in exc.errors()
    ]  # the rejected input values are deliberately not echoed back
    return error_response(422, "VALIDATION_ERROR", "The request is invalid", details)


async def _http(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return error_response(exc.status_code, _HTTP_CODES.get(exc.status_code, "HTTP_ERROR"), str(exc.detail))


async def _parser(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ParserError)
    return error_response(422, "INVALID_TEST_FILE", exc.message, _messages(exc.errors))


async def _blocked(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, MigrationBlockedError)
    return error_response(409, "MIGRATION_BLOCKED", str(exc).split(":")[0], _messages(exc.reasons))


async def _generation(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, GenerationError)
    return error_response(422, "GENERATION_FAILED", "The test case cannot be generated", _messages(exc.problems))


def _simple(status: int, code: str):  # noqa: ANN202
    async def handler(_: Request, exc: Exception) -> JSONResponse:
        return error_response(status, code, str(exc) or code.replace("_", " ").capitalize())

    return handler


async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return error_response(500, "INTERNAL_ERROR", "An unexpected error occurred")


async def _store(request: Request, exc: Exception) -> JSONResponse:
    log.error("Migration store error on %s %s: %s", request.method, request.url.path, exc)
    return error_response(500, "STORE_ERROR", str(exc))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(StarletteHTTPException, _http)
    app.add_exception_handler(ParserError, _parser)
    app.add_exception_handler(MigrationBlockedError, _blocked)
    app.add_exception_handler(GenerationError, _generation)
    app.add_exception_handler(UnsupportedUploadError, _simple(415, "UNSUPPORTED_MEDIA_TYPE"))
    app.add_exception_handler(MigrationNotFoundError, _simple(404, "MIGRATION_NOT_FOUND"))
    app.add_exception_handler(MigrationStateError, _simple(409, "INVALID_STATE"))
    app.add_exception_handler(MigrationBusyError, _simple(409, "MIGRATION_BUSY"))
    app.add_exception_handler(MigrationStoreError, _store)
    app.add_exception_handler(RepairNotPossibleError, _simple(422, "REPAIR_NOT_POSSIBLE"))
    app.add_exception_handler(QTestError, _simple(502, "QTEST_ERROR"))
    app.add_exception_handler(ExecutionCapacityError, _simple(429, "EXECUTION_CAPACITY_REACHED"))
    app.add_exception_handler(NotImplementedError, _simple(501, "NOT_IMPLEMENTED"))
    app.add_exception_handler(Exception, _unexpected)
