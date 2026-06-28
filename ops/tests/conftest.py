"""Shared fixtures for ops tests: migrated PostgreSQL testcontainer + ORM session + TestClient.

The ops app's lifespan builds its engine from DATABASE_URL_OWNER (falling back to DATABASE_URL),
so the TestClient reads the SAME database the `session` fixture seeds. A network-free FixtureFetcher
(scrape's real saved fixtures for race 202406050911) drives the worker without hitting netkeiba.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.urls import entries_url, result_url, win_odds_url
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_DIR = REPO_ROOT / "db"
SCRAPE_FIXTURES = REPO_ROOT / "scrape" / "tests" / "fixtures" / "real"

REAL_RID = "202406050911"  # Hopeful S, 中山 2024-12-28 11R, 18 horses (scrape fixtures)


@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("postgres:16", driver="psycopg") as container:
        yield container


@pytest.fixture(scope="session")
def database_url(pg_container: PostgresContainer) -> str:
    url = pg_container.get_connection_url()
    os.environ["DATABASE_URL"] = url
    os.environ.pop("DATABASE_URL_OWNER", None)  # local/dev fallback to DATABASE_URL
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


@pytest.fixture
def client(_migrated: str) -> TestClient:
    from horseracing_ops.app import app
    with TestClient(app) as c:
        yield c


def _read(name: str) -> str:
    return (SCRAPE_FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def fixture_fetcher() -> FixtureFetcher:
    """Maps the ops runner's URLs for REAL_RID to the saved entries/odds/results fixtures."""
    return FixtureFetcher({
        entries_url(REAL_RID): _read(f"entries_{REAL_RID}.html"),
        win_odds_url(REAL_RID): _read(f"odds_{REAL_RID}.json"),
        result_url(REAL_RID): _read(f"results_{REAL_RID}.html"),
    })


@pytest.fixture(autouse=True)
def _truncate_between_tests(request):
    is_integration = request.node.get_closest_marker("integration") is not None
    engine: Engine | None = request.getfixturevalue("engine") if is_integration else None
    yield
    if engine is None:
        return
    with engine.begin() as conn:
        conn.execute(text(
            """
            DO $$
            DECLARE r record;
            BEGIN
                FOR r IN SELECT tablename FROM pg_tables
                         WHERE schemaname='public' AND tablename<>'alembic_version'
                LOOP EXECUTE 'TRUNCATE TABLE '||quote_ident(r.tablename)||' RESTART IDENTITY CASCADE';
                END LOOP;
            END $$;
            """
        ))
