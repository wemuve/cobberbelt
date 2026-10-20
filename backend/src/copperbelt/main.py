"""FastAPI application: middleware, error handling, health endpoints and routers."""

from __future__ import annotations

import logging
import time
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from copperbelt import __version__
from copperbelt.api.ratelimit import limiter
from copperbelt.api.v1 import auth, documents, evidence, org, projects, providers
from copperbelt.config import get_settings
from copperbelt.db import system_session
from copperbelt.errors import AppError
from copperbelt.models.ops import Job, JobStatus
from copperbelt.storage import get_store

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)
log = structlog.get_logger()


def _error(status: int, code: str, message: str, request: Request, detail=None) -> JSONResponse:  # noqa: ANN001
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "detail": detail,
                           "request_id": getattr(request.state, "request_id", None)}},
    )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Copperbelt Intelligence API",
        version=__version__,
        description="Evidence-traceable geological and mining project intelligence. "
                    "All tenant endpoints require `Authorization: Bearer <token>` and `X-Org-Id`.",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Org-Id", "X-Requested-With", "Idempotency-Key"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # noqa: ANN001, ANN202
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        request.state.request_id = rid[:64]
        structlog.contextvars.bind_contextvars(request_id=request.state.request_id)
        start = time.perf_counter()
        try:
            if request.url.path.startswith("/api/v1/") and request.url.path not in ("/api/v1/health", "/api/v1/ready"):
                client = request.client.host if request.client else "unknown"
                limiter.hit(f"api:{client}", settings.rate_limit_per_minute)
            response = await call_next(request)
        except AppError as exc:
            response = _error(exc.status_code, exc.code, exc.message, request, exc.detail)
        finally:
            structlog.contextvars.clear_contextvars()
        elapsed = (time.perf_counter() - start) * 1000
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'" if not request.url.path.startswith("/api/docs") else response.headers.get("Content-Security-Policy", "")
        if settings.environment in ("staging", "production"):
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        log.info("http.request", method=request.method, path=request.url.path, status=response.status_code,
                 duration_ms=round(elapsed, 1), request_id=request.state.request_id)
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, request, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return _error(422, "validation_failed", "Request validation failed", request, errors)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, "http_error", str(exc.detail), request)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return _error(500, "internal_error", "An unexpected error occurred. Quote the request id when reporting it.", request)

    @app.get("/api/v1/health", tags=["system"])
    def health() -> dict:
        """Liveness: the process is running. Says nothing about dependencies."""
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/ready", tags=["system"])
    def ready() -> JSONResponse:
        """Readiness: database, migrations, object storage and job queue."""
        checks: dict[str, dict] = {}
        ok = True
        try:
            with system_session() as s:
                s.execute(text("SELECT 1"))
                rev = s.execute(text("SELECT version_num FROM alembic_version")).scalar()
                checks["database"] = {"ok": True, "migration": rev}
                q = dict(s.execute(select(Job.status, func.count()).group_by(Job.status)).all())
                dead = q.get(JobStatus.dead, 0)
                checks["jobs"] = {"ok": True, "queued": q.get(JobStatus.queued, 0), "running": q.get(JobStatus.running, 0),
                                  "failed_retrying": q.get(JobStatus.failed, 0), "dead": dead}
        except Exception as exc:  # noqa: BLE001
            ok = False
            checks["database"] = {"ok": False, "error": type(exc).__name__}
        store_ok, store_detail = get_store().health()
        checks["object_storage"] = {"ok": store_ok, "backend": get_store().name, "detail": store_detail if store_ok else "unavailable"}
        ok = ok and store_ok
        return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)

    for r in (auth.router, org.router, documents.router, evidence.router, projects.router, providers.router):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
