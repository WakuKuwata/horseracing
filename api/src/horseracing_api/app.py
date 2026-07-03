"""FastAPI app: read-only prediction-serving API (Feature 014).

Lifespan creates the app-scoped engine + sessionmaker once (reusing db.session helpers). All routes
live under /api/v1 and are READ-ONLY (per-request DB read-only session, see deps.py). Typed error
bodies {status, code, detail}; pure-helper exceptions (009/010) are mapped to 409/422 rather than
surfacing as 500. OpenAPI/`/docs` are auto-generated from the pydantic response schemas — this is
the contract the React/Vite front (015) consumes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from horseracing_db.session import create_db_engine, create_session_factory
from horseracing_probability.market_odds import MarketOddsError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import API_PREFIX, API_VERSION
from .routers import (
    calibration,
    coverage,
    diagnostics,
    horses,
    importance,
    jobs,
    jockeys,
    models,
    odds,
    predictions,
    races,
    recommendations,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_db_engine()
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    try:
        yield
    finally:
        engine.dispose()


app = FastAPI(
    title="horseracing prediction-serving API",
    version=API_VERSION,
    description="Read-only JSON serving of races / predictions / odds / recommendations (014).",
    lifespan=lifespan,
)


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


@app.exception_handler(MarketOddsError)
async def _market_odds_error(request: Request, exc: MarketOddsError) -> JSONResponse:
    # estimated odds need >=2 valid win odds; treat as a state issue, never an unhandled 500
    return _error(409, "odds_unavailable", str(exc))


@app.exception_handler(ValueError)
async def _value_error(request: Request, exc: ValueError) -> JSONResponse:
    # 009 joint_probabilities raises on empty/degenerate probs -> typed 409, not 500
    return _error(409, "unprocessable_state", str(exc))


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # normalize FastAPI's validation errors to the same {status, code, detail} ErrorBody shape
    return _error(422, "validation_error", str(exc.errors()))


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error(exc.status_code, "http_error", str(exc.detail))


app.include_router(races.router, prefix=API_PREFIX)
app.include_router(predictions.router, prefix=API_PREFIX)
app.include_router(odds.router, prefix=API_PREFIX)
app.include_router(recommendations.router, prefix=API_PREFIX)
app.include_router(calibration.router, prefix=API_PREFIX)
app.include_router(importance.router, prefix=API_PREFIX)
app.include_router(models.router, prefix=API_PREFIX)
app.include_router(coverage.router, prefix=API_PREFIX)
app.include_router(jobs.router, prefix=API_PREFIX)
app.include_router(diagnostics.router, prefix=API_PREFIX)
app.include_router(horses.router, prefix=API_PREFIX)
app.include_router(jockeys.router, prefix=API_PREFIX)
