"""Feature 174: decision_quotes schema, uniqueness and append-only enforcement."""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from horseracing_db.models import DecisionQuote, Race

pytestmark = pytest.mark.integration

_COLUMNS = {
    "decision_quote_id", "race_id", "captured_at", "official_at", "post_time", "seconds_to_post",
    "capture_target", "source", "n", "field", "pred", "quote_digest", "field_digest",
    "features_sha256", "ensemble_version", "ensemble_manifest", "ensemble_manifest_sha256",
    "single_version", "single_booster", "single_booster_sha256", "mix_version", "mix_status",
    "mix_coefficient_year", "logic_version", "policy_version", "run_id", "created_at",
}
POST = datetime.datetime(2026, 10, 31, 6, 0, tzinfo=datetime.UTC)


def _row(race_id: str = "202610310501", **over) -> DecisionQuote:
    base = dict(
        race_id=race_id,
        captured_at=POST - datetime.timedelta(seconds=181),
        official_at=POST - datetime.timedelta(seconds=200),
        post_time=POST,
        seconds_to_post=181,
        capture_target="t_minus_3",
        source="netkeiba",
        n=2,
        field=[
            {"horse_id": "H01", "horse_number": 1, "win_odds": 2.5, "popularity": 1},
            {"horse_id": "H02", "horse_number": 2, "win_odds": 4.0, "popularity": 2},
        ],
        pred=[
            {"horse_id": "H01", "horse_number": 1, "p_ens": 0.4, "ev_ens": 1.0},
            {"horse_id": "H02", "horse_number": 2, "p_ens": 0.2, "ev_ens": 0.8},
        ],
        quote_digest="q" * 64,
        field_digest="f" * 64,
        features_sha256="s" * 64,
        ensemble_version="mev-ens15-v1",
        ensemble_manifest="ensemble_2026.json",
        ensemble_manifest_sha256="e" * 64,
        single_version="mev-binary-v2",
        single_booster="model_2026.txt",
        single_booster_sha256="b" * 64,
        mix_version="mix-ens15-v1",
        mix_status="available",
        mix_coefficient_year=2026,
        logic_version="near-post-v1",
        policy_version="near_post_policy_v1",
        run_id=uuid.uuid4(),
    )
    base.update(over)
    return DecisionQuote(**base)


def _seed(session, race_id: str = "202610310501") -> None:
    session.add(Race(race_id=race_id, race_number=5, race_date=POST.date(), post_time=POST))
    session.flush()


def test_table_columns_and_indexes(engine):
    insp = inspect(engine)
    assert {c["name"] for c in insp.get_columns("decision_quotes")} == _COLUMNS
    uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("decision_quotes")}
    assert ("race_id",) in uniques
    names = {i["name"] for i in insp.get_indexes("decision_quotes")}
    assert "ix_decision_quotes_captured_at" in names


def test_one_row_per_race(session):
    _seed(session)
    session.add(_row())
    session.flush()
    session.add(_row(quote_digest="z" * 64))
    with pytest.raises(IntegrityError):
        session.flush()


def test_rows_are_append_only(session):
    _seed(session)
    row = _row()
    session.add(row)
    session.flush()
    row.mix_status = "disabled"
    with pytest.raises(DBAPIError, match="append-only"):
        session.flush()
    session.rollback()
    _seed(session)
    session.add(_row())
    session.flush()
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("DELETE FROM decision_quotes"))
    session.rollback()
    _seed(session)
    session.add(_row())
    session.flush()
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("TRUNCATE decision_quotes"))
    session.rollback()


def test_checks_reject_negative_seconds_and_single_horse(session):
    _seed(session)
    session.add(_row(seconds_to_post=-1))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    _seed(session)
    session.add(_row(n=1))
    with pytest.raises(IntegrityError):
        session.flush()
