"""FastAPI app for the ops (data-refresh) service (Feature 024).

Separate app/port from the read-only 014 API. Runs as the OWNER DB role (read+write) and exposes
ONLY the refresh-accept + job-status endpoints under /ops/v1. Typed error bodies {status, code,
detail}; validation errors (e.g. a non-date path) become 422 rather than an unhandled 500.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import API_PREFIX, API_VERSION
from .deps import create_ops_engine
from .routers import jobs, predict, refresh


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_ops_engine()
    app.state.engine = engine
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield
    finally:
        engine.dispose()


app = FastAPI(
    title="horseracing ops (data-refresh) API",
    version=API_VERSION,
    description="On-demand netkeiba refresh — enqueue + job status (Feature 024). Write path, "
                "separate from the read-only 014 API.",
    lifespan=lifespan,
)


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"status": status, "code": code, "detail": detail})


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error(422, "validation_error", str(exc.errors()))


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error(exc.status_code, "http_error", str(exc.detail))


app.include_router(refresh.router, prefix=API_PREFIX)
app.include_router(predict.router, prefix=API_PREFIX)
app.include_router(jobs.router, prefix=API_PREFIX)
