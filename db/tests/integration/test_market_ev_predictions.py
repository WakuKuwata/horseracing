"""Feature 137: market_ev_predictions schema contract and database-level invariants (plan 0.2).

The table holds the latest expected return (期待回収率) of a SEPARATE market-aware model per
(race, model_version, horse). It is replaced per race (no history — constitution V), so unlike
purchase_records it must stay mutable; the CHECK constraints are the database's last line against
writing a value the display would misread (a probability outside (0, 1), odds below the 1.0
payout floor, a negative expected return).
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from horseracing_db.models import MarketEvPrediction, Race

pytestmark = pytest.mark.integration

_TABLE = "market_ev_predictions"
_RID = "202609270511"
_COLUMNS = {
    # name: nullable
    "race_id": False,
    "horse_id": False,
    "model_version": False,
    "horse_number": True,
    "win_prob": False,
    "odds_used": False,
    "expected_return": False,
    "odds_observed_at": False,
    "result_pending_at_compute": False,
    "booster": False,
    "booster_sha256": False,
    "logic_version": False,
    "run_id": False,
    "computed_at": False,
}
_CHECKS = {
    "ck_market_ev_predictions_win_prob",
    "ck_market_ev_predictions_odds_used",
    "ck_market_ev_predictions_expected_return",
}
_INDEX = "ix_market_ev_predictions_model_version_computed_at"
#: created after 0018 — they also disappear when downgrading to 0017 (Feature 138)
_TABLES_ADDED_AFTER_0018 = {"attention_race_scans", "attention_picks", "attention_checkpoints"}


def _seed_race(session: Session) -> None:
    # Flush the parent race before any child row (repo convention, see test_chaos_tables).
    session.add(Race(race_id=_RID, race_number=11, race_date=datetime.date(2026, 9, 27)))
    session.flush()


def _row(**overrides) -> MarketEvPrediction:
    values = dict(
        race_id=_RID,
        horse_id="2021104567",
        model_version="mev-binary-v2",
        horse_number=3,
        win_prob=Decimal("0.0825"),
        odds_used=Decimal("15.2"),
        expected_return=Decimal("1.254"),
        odds_observed_at=datetime.datetime(2026, 9, 27, 5, 40, tzinfo=datetime.UTC),
        result_pending_at_compute=True,
        booster="model_2026.txt",
        booster_sha256="a" * 64,
        logic_version="mev-v1;features=roi-explore-2026-09;drop=sameday,weightlive;data>=2007",
        run_id=uuid.uuid4(),
    )
    values.update(overrides)
    return MarketEvPrediction(**values)


def test_columns_and_nullability_match_contract(engine):
    columns = {c["name"]: c for c in inspect(engine).get_columns(_TABLE)}
    assert {name: c["nullable"] for name, c in columns.items()} == _COLUMNS
    # computed_at is the single audit timestamp and defaults to now()
    assert "now()" in str(columns["computed_at"]["default"])
    # the table deliberately has no TimestampMixin pair
    assert "created_at" not in columns and "updated_at" not in columns


def test_primary_key_check_constraints_and_index(engine):
    inspector = inspect(engine)
    pk = inspector.get_pk_constraint(_TABLE)
    assert pk["name"] == "pk_market_ev_predictions"
    assert pk["constrained_columns"] == ["race_id", "model_version", "horse_id"]

    assert {c["name"] for c in inspector.get_check_constraints(_TABLE)} == _CHECKS

    indexes = {i["name"]: i for i in inspector.get_indexes(_TABLE)}
    assert indexes[_INDEX]["column_names"] == ["model_version", "computed_at"]
    assert indexes[_INDEX]["unique"] is False


def test_only_races_is_referenced(engine):
    """FK to races only: not to horses (regenerable derived value, outside the 067 re-key) and
    not to model_versions (outside the main pipeline's single-active contract)."""
    fks = inspect(engine).get_foreign_keys(_TABLE)
    assert [(fk["referred_table"], fk["constrained_columns"]) for fk in fks] == [
        ("races", ["race_id"])
    ]


def test_insert_defaults_computed_at_and_needs_no_horse_or_model_row(session: Session):
    _seed_race(session)
    # neither a horses row nor a model_versions row exists for these ids
    session.add(_row())
    session.commit()
    stored = session.execute(
        text(f"SELECT computed_at, run_id FROM {_TABLE} WHERE race_id = :r"), {"r": _RID}
    ).one()
    assert stored.computed_at is not None
    assert isinstance(stored.run_id, uuid.UUID)


def test_primary_key_rejects_a_second_row_for_the_same_horse_and_model(session: Session):
    _seed_race(session)
    session.add(_row())
    session.commit()
    session.add(_row(expected_return=Decimal("0.9")))
    with pytest.raises(IntegrityError, match="pk_market_ev_predictions"):
        session.commit()
    session.rollback()


def test_same_horse_under_another_model_version_is_a_separate_row(session: Session):
    _seed_race(session)
    session.add_all([_row(), _row(model_version="mev-binary-v3")])
    session.commit()
    n = session.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar_one()
    assert n == 2


def test_unknown_race_is_rejected_by_foreign_key(session: Session):
    session.add(_row(race_id="209912319912"))
    with pytest.raises(IntegrityError, match="fk_market_ev_predictions_race_id_races"):
        session.commit()
    session.rollback()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"win_prob": Decimal("0")}, "ck_market_ev_predictions_win_prob"),
        ({"win_prob": Decimal("1")}, "ck_market_ev_predictions_win_prob"),
        ({"win_prob": Decimal("-0.1")}, "ck_market_ev_predictions_win_prob"),
        ({"odds_used": Decimal("0.99")}, "ck_market_ev_predictions_odds_used"),
        ({"expected_return": Decimal("-0.001")}, "ck_market_ev_predictions_expected_return"),
    ],
)
def test_check_constraints_reject_out_of_range_values(
    session: Session, overrides: dict, constraint: str
):
    _seed_race(session)
    session.add(_row(**overrides))
    with pytest.raises(IntegrityError, match=constraint):
        session.commit()
    session.rollback()


def test_check_constraint_boundaries_are_accepted(session: Session):
    """odds_used = 1.0 (the payout floor) and expected_return = 0 are legal values."""
    _seed_race(session)
    session.add_all([
        _row(horse_id="H-floor", odds_used=Decimal("1.0"), expected_return=Decimal("0.95")),
        _row(horse_id="H-zero", expected_return=Decimal("0")),
    ])
    session.commit()
    n = session.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar_one()
    assert n == 2


def test_race_rows_are_replaced_in_one_transaction(session: Session):
    """Latest value only (constitution V): a recompute DELETEs the race's rows for the model and
    INSERTs the new ones — the table must not be append-only."""
    _seed_race(session)
    first_run = uuid.uuid4()
    session.add_all([_row(run_id=first_run), _row(horse_id="H2", horse_number=4, run_id=first_run)])
    session.commit()

    second_run = uuid.uuid4()
    session.execute(
        text(f"DELETE FROM {_TABLE} WHERE race_id = :r AND model_version = :mv"),
        {"r": _RID, "mv": "mev-binary-v2"},
    )
    session.add(_row(run_id=second_run, expected_return=Decimal("1.31")))
    session.commit()

    rows = session.execute(
        text(f"SELECT run_id, expected_return FROM {_TABLE} WHERE race_id = :r"), {"r": _RID}
    ).all()
    assert [(r.run_id, r.expected_return) for r in rows] == [(second_run, Decimal("1.31"))]


def test_downgrade_to_0017_removes_the_table_and_upgrade_restores_it(
    alembic_cfg, engine, _migrated
):
    tables_at_head = set(inspect(engine).get_table_names())
    assert _TABLE in tables_at_head
    try:
        command.downgrade(alembic_cfg, "0017_purchase_records")
        inspector = inspect(engine)
        expected = tables_at_head - {_TABLE} - _TABLES_ADDED_AFTER_0018
        assert set(inspector.get_table_names()) == expected
        with engine.connect() as conn:
            leftover_index = conn.execute(
                text("SELECT to_regclass(:name)"), {"name": _INDEX}
            ).scalar_one()
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert leftover_index is None
        assert version == "0017_purchase_records"

        command.upgrade(alembic_cfg, "head")
        assert set(inspect(engine).get_table_names()) == tables_at_head
    finally:
        command.upgrade(alembic_cfg, "head")
