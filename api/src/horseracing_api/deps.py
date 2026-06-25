"""Per-request READ-ONLY DB session dependency (Feature 014, constitution II/V).

The session is opened in a DB-level READ ONLY transaction (`SET TRANSACTION READ ONLY`), so any
accidental write is rejected by Postgres — stronger than relying on rollback alone. The app-scoped
sessionmaker is created once in the FastAPI lifespan (app.py) and stored on app.state; this
dependency yields one Session per request and always rolls back + closes (never commits).
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    session: Session = factory()
    try:
        # DB-level read-only: Postgres rejects any INSERT/UPDATE/DELETE in this transaction.
        session.execute(text("SET TRANSACTION READ ONLY"))
        yield session
    finally:
        session.rollback()  # never commit; undo anything (defence in depth)
        session.close()
