"""Per-request READ+WRITE DB session for the ops service (Feature 024).

Unlike the 014 API (which opens a DB-level READ ONLY transaction, deps.py), the ops service runs as
the OWNER role and must write (enqueue jobs). The app-scoped sessionmaker is built once in the
FastAPI lifespan from DATABASE_URL_OWNER (falling back to DATABASE_URL for local/dev). Each request
gets one Session; on success the router commits, otherwise we roll back.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from fastapi import Request
from horseracing_db.session import create_db_engine
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def owner_database_url() -> str:
    """Prefer the owner (read+write) URL; fall back to DATABASE_URL for single-role local/dev."""
    url = os.getenv("DATABASE_URL_OWNER") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_OWNER (or DATABASE_URL) is not set")
    return url


def create_ops_engine() -> Engine:
    return create_db_engine(owner_database_url())


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
