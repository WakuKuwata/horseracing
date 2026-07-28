"""Shared fixtures: migrated PostgreSQL testcontainer (Feature 001 schema @ head)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_DIR = REPO_ROOT / "db"


class SpyChaosFetcher:
    """Fresh-odds test double whose call count proves whether a fetch occurred."""

    source = "fixture-adapter"

    def __init__(
        self,
        payload: str,
        *,
        on_get: Callable[[], None] | None = None,
    ) -> None:
        self.payload = payload
        self.on_get = on_get
        self.calls = 0

    def get(self, _url: str, *, use_cache: bool = True) -> str:
        assert use_cache is False
        self.calls += 1
        if self.on_get is not None:
            self.on_get()
        return self.payload


@pytest.fixture
def spy_fetcher():
    """Build an independently counted fetcher for each eligibility attempt."""

    def make(
        payload: str,
        *,
        on_get: Callable[[], None] | None = None,
    ) -> SpyChaosFetcher:
        return SpyChaosFetcher(payload, on_get=on_get)

    return make


@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("postgres:16", driver="psycopg") as container:
        yield container


@pytest.fixture(scope="session")
def database_url(pg_container: PostgresContainer) -> str:
    url = pg_container.get_connection_url()
    os.environ["DATABASE_URL"] = url
    return url


@pytest.fixture(scope="session")
def _migrated(database_url: str) -> str:
    cfg = Config(str(DB_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(DB_DIR / "migrations"))
    os.environ["DATABASE_URL"] = database_url
    command.upgrade(cfg, "head")
    return database_url


@pytest.fixture(scope="session")
def engine(_migrated: str) -> Engine:
    eng = create_engine(_migrated)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Session:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    sess = factory()
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()


@pytest.fixture(autouse=True)
def _truncate_between_tests(request):
    is_integration = request.node.get_closest_marker("integration") is not None
    engine: Engine | None = request.getfixturevalue("engine") if is_integration else None
    yield
    if engine is None:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DO $$
                DECLARE r record;
                BEGIN
                    FOR r IN
                        SELECT tablename FROM pg_tables
                        WHERE schemaname = 'public' AND tablename <> 'alembic_version'
                    LOOP
                        EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename)
                                || ' RESTART IDENTITY CASCADE';
                    END LOOP;
                END $$;
                """
            )
        )
