"""Feature 140 (T001): races.win_odds_official_at / win_odds_observed_at — schema contract.

Two nullable TIMESTAMPTZ columns, the same shape as the 複勝 precedent (0014). Existing rows stay
NULL (no backfill: a proxy must not be dressed up as provenance), the ORM round-trips tz-aware
values, and the migration is reversible (downgrade to 0020 drops exactly these two columns).
"""

from __future__ import annotations

import datetime

import pytest
from alembic import command
from sqlalchemy import inspect, text

from horseracing_db.models import Race

pytestmark = pytest.mark.integration

_COLUMNS = ("win_odds_official_at", "win_odds_observed_at")
_RID = "202610110511"


def _race_columns(engine) -> dict[str, dict]:
    return {c["name"]: c for c in inspect(engine).get_columns("races")}


def test_columns_exist_nullable_and_timezone_aware(engine):
    columns = _race_columns(engine)
    for name in _COLUMNS:
        assert name in columns, name
        assert columns[name]["nullable"] is True
        assert columns[name]["type"].timezone is True
        assert columns[name]["default"] is None  # no server default: NULL means "unknown"


def test_existing_row_is_null_and_orm_round_trips(session):
    session.add(Race(race_id=_RID, race_number=11, race_date=datetime.date(2026, 10, 11)))
    session.commit()
    race = session.get(Race, _RID)
    assert race.win_odds_official_at is None and race.win_odds_observed_at is None

    official = datetime.datetime(2026, 10, 11, 6, 50, 17, tzinfo=datetime.UTC)
    observed = datetime.datetime(2026, 10, 11, 6, 51, 2, tzinfo=datetime.UTC)
    race.win_odds_official_at = official
    race.win_odds_observed_at = observed
    session.commit()
    session.expire_all()

    stored = session.get(Race, _RID)
    assert stored.win_odds_official_at == official
    assert stored.win_odds_observed_at == observed
    assert stored.win_odds_official_at.tzinfo is not None
    # the place-quote provenance is a different fact and is untouched
    assert stored.place_odds_official_at is None and stored.place_odds_observed_at is None


def test_downgrade_to_0020_drops_the_columns_and_upgrade_restores_them(
    alembic_cfg, engine, _migrated
):
    at_head = set(_race_columns(engine))
    assert set(_COLUMNS) <= at_head
    try:
        command.downgrade(alembic_cfg, "0020_official_win_payouts")
        assert set(_race_columns(engine)) == at_head - set(_COLUMNS)
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            tables = set(inspect(conn).get_table_names())
        assert version == "0020_official_win_payouts"
        assert "official_win_payouts" in tables  # only the columns go, not the 139 table

        command.upgrade(alembic_cfg, "head")
        assert set(_race_columns(engine)) == at_head
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "0021_win_odds_provenance"
    finally:
        command.upgrade(alembic_cfg, "head")
