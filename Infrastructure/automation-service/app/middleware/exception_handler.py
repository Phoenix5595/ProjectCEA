"""Global exception handler middleware for FastAPI.

Provides consistent error response format across all API endpoints.
"""

from __future__ import annotations

from collections.abc import Callable
import traceback
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class APIError(Exception):
    """Base exception for API errors with structured error response."""

    def __init__(
        self,
        status_code: int,
        message: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.status_code = status_code
        self.message = message
        self.error_code = error_code
        self.details = details
        super().__init__(message)


class NotFoundError(APIError):
    """Resource not found error."""

    def __init__(self, message: str = "Resource not found", error_code: str = "NOT_FOUND"):
        super().__init__(status_code=404, message=message, error_code=error_code)


class ValidationAPIError(APIError):
    """Validation error."""

    def __init__(self, message: str = "Validation error", details: dict[str, Any] | None = None):
        super().__init__(
            status_code=422, message=message, error_code="VALIDATION_ERROR", details=details
        )


class ConflictError(APIError):
    """Conflict error (e.g., resource already exists)."""

    def __init__(self, message: str = "Resource conflict", error_code: str = "CONFLICT"):
        super().__init__(status_code=409, message=message, error_code=error_code)


def _build_error_response(
    status_code: int,
    message: str,
    error_code: str | None = None,
    details: dict[str, Any] | None = None,
    request_path: str | None = None,
) -> dict[str, Any]:
    """Build a structured error response dictionary."""
    response = {
        "error": {
            "status_code": status_code,
            "message": message,
        }
    }
    if error_code:
        response["error"]["error_code"] = error_code
    if details:
        response["error"]["details"] = details
    if request_path:
        response["error"]["request_path"] = request_path
    return response


async def exception_handler_middleware(request: Request, call_next: Callable) -> Response:
    """Middleware to handle exceptions and return consistent error responses.

    This middleware catches all exceptions and formats them into a consistent
    JSON error response structure.

    Args:
        request: FastAPI request object
        call_next: Next middleware/handler in chain

    Returns:
        JSONResponse with structured error or normal response
    """
    try:
        return await call_next(request)

    except ValidationError as e:
        # Pydantic validation errors
        details = {}
        for error in e.errors():
            loc = ".".join(str(part) for part in error["loc"])
            details[loc] = error["msg"]

        logger.warning(
            f"Validation error on {request.method} {request.url.path}: {e}",
            extra={"errors": e.errors()},
        )

        return JSONResponse(
            status_code=422,
            content=_build_error_response(
                status_code=422,
                message="Request validation failed",
                error_code="VALIDATION_ERROR",
                details=details,
                request_path=request.url.path,
            ),
        )

    except APIError as e:
        # Custom API errors
        logger.warning(
            f"API error on {request.method} {request.url.path}: {e.message}",
            extra={"error_code": e.error_code, "details": e.details},
        )

        return JSONResponse(
            status_code=e.status_code,
            content=_build_error_response(
                status_code=e.status_code,
                message=e.message,
                error_code=e.error_code,
                details=e.details,
                request_path=request.url.path,
            ),
        )

    except HTTPException:
        # Re-raise HTTPException to let FastAPI handle it normally
        raise

    except Exception as e:
        # Unexpected errors - log full traceback
        tb = traceback.format_exc()
        logger.error(
            f"Unhandled exception on {request.method} {request.url.path}: {e}\n{tb}",
            exc_info=True,
        )

        return JSONResponse(
            status_code=500,
            content=_build_error_response(
                status_code=500,
                message="Internal server error",
                error_code="INTERNAL_ERROR",
                details={"type": type(e).__name__} if not isinstance(e, Exception) else None,
                request_path=request.url.path,
            ),
        )
