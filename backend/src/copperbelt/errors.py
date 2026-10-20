"""Uniform error responses: {"error": {"code", "message", "detail", "request_id"}}."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, detail: Any = None, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        if code:
            self.code = code


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


class TooManyRequests(AppError):
    status_code = 429
    code = "rate_limited"


class ProviderUnavailable(AppError):
    """An external provider is unconfigured, unreachable or refused the request."""

    status_code = 503
    code = "provider_unavailable"
