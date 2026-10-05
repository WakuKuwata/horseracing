"""Feature 139 (T002): official_win_payouts schema contract and database-level invariants.

The table holds the official 単勝 payout per winning horse, read from the netkeiba result page.
It is overwritable (an official correction must be followed — unlike the 138 prospective record),
so the guarantees that matter are the shape (one row per race × 馬番, dead heat = two rows), the
CHECK constraints that refuse a value the 138 settlement would misread, and the writer-independent
updated_at trigger.
"""

from __future__ import annotations

import datetime

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from horseracing_db.models import OfficialWinPayout, Race

pytestmark = pytest.mark.integration

_TABLE = "official_win_payouts"
_RID = "202610040511"
_COLUMNS = {
    # name: nullable
    "race_id": False,
    "horse_number": False,
    "payout_yen": False,
    "source": False,
    "observed_at": False,
    "html_sha256": True,
    "created_at": False,
    "updated_at": False,
}
_CHECKS = {
    "ck_official_win_payouts_horse_number",
    "ck_official_win_payouts_payout_yen",
    "ck_official_win_payouts_source",
}
_OBSERVED = datetime.datetime(2026, 10, 4, 7, 30, tzinfo=datetime.UTC)


def _seed_race(session: Session) -> None:
    # Flush the parent race before any child row (repo convention, see test_chaos_tables).
    session.add(Race(race_id=_RID, race_number=11, race_date=datetime.date(2026, 10, 4)))
    session.flush()


def _row(**overrides) -> OfficialWinPayout:
    values = dict(race_id=_RID, horse_number=7, payout_yen=1520, observed_at=_OBSERVED,
                  html_sha256="b" * 64)
    values.update(overrides)
    return OfficialWinPayout(**values)


def test_columns_and_nullability_match_contract(engine):
    columns = {c["name"]: c for c in inspect(engine).get_columns(_TABLE)}
    assert {name: c["nullable"] for name, c in columns.items()} == _COLUMNS
    assert "netkeiba_result" in str(columns["source"]["default"])
    assert "now()" in str(columns["created_at"]["default"])
    assert "now()" in str(columns["updated_at"]["default"])


def test_primary_key_checks_and_foreign_key(engine):
    inspector = inspect(engine)
    pk = inspector.get_pk_constraint(_TABLE)
    assert pk["name"] == "pk_official_win_payouts"
    assert pk["constrained_columns"] == ["race_id", "horse_number"]
    assert {c["name"] for c in inspector.get_check_constraints(_TABLE)} == _CHECKS
    fks = inspector.get_foreign_keys(_TABLE)
    assert [(fk["referred_table"], fk["constrained_columns"]) for fk in fks] == [
        ("races", ["race_id"])
    ]


def test_updated_at_trigger_exists(engine):
    with engine.connect() as conn:
        names = set(conn.execute(text(
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
            "AND tgrelid = 'official_win_payouts'::regclass"
        )).scalars())
    assert names == {"trg_official_win_payouts_set_updated_at"}


def test_insert_defaults_source_and_timestamps(session: Session):
    _seed_race(session)
    session.add(_row(html_sha256=None))
    session.commit()
    stored = session.execute(text(
        f"SELECT source, html_sha256, created_at, updated_at FROM {_TABLE} WHERE race_id = :r"
    ), {"r": _RID}).one()
    assert stored.source == "netkeiba_result"
    assert stored.html_sha256 is None
    assert stored.created_at is not None and stored.updated_at is not None


def test_dead_heat_is_one_row_per_winning_horse(session: Session):
    _seed_race(session)
    session.add_all([_row(horse_number=3, payout_yen=200), _row(horse_number=7, payout_yen=350)])
    session.commit()
    rows = session.execute(text(
        f"SELECT horse_number, payout_yen FROM {_TABLE} WHERE race_id = :r ORDER BY horse_number"
    ), {"r": _RID}).all()
    assert [tuple(r) for r in rows] == [(3, 200), (7, 350)]


def test_primary_key_rejects_a_second_row_for_the_same_horse(session: Session):
    _seed_race(session)
    session.add(_row())
    session.commit()
    session.add(_row(payout_yen=1600))
    with pytest.raises(IntegrityError, match="pk_official_win_payouts"):
        session.commit()
    session.rollback()


def test_unknown_race_is_rejected_by_foreign_key(session: Session):
    session.add(_row(race_id="209912319912"))
    with pytest.raises(IntegrityError, match="fk_official_win_payouts_race_id_races"):
        session.commit()
    session.rollback()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"horse_number": 0}, "ck_official_win_payouts_horse_number"),
        ({"horse_number": -3}, "ck_official_win_payouts_horse_number"),
        ({"payout_yen": 99}, "ck_official_win_payouts_payout_yen"),
        ({"payout_yen": 0}, "ck_official_win_payouts_payout_yen"),
        ({"source": "netkeiba"}, "ck_official_win_payouts_source"),
        ({"source": "jra_van"}, "ck_official_win_payouts_source"),
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
    """馬番 1 and 元返し (100 yen) are legal values."""
    _seed_race(session)
    session.add(_row(horse_number=1, payout_yen=100))
    session.commit()
    n = session.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar_one()
    assert n == 1


def test_null_observed_at_is_rejected(session: Session):
    _seed_race(session)
    session.add(_row(observed_at=None))
    with pytest.raises(IntegrityError, match="observed_at"):
        session.commit()
    session.rollback()


def test_overwrite_is_allowed_and_bumps_updated_at(session: Session):
    """Not append-only: an official correction overwrites the row, and the trigger (not the
    writer) moves updated_at forward while created_at stays."""
    _seed_race(session)
    old = datetime.datetime(2026, 10, 4, 8, 0, tzinfo=datetime.UTC)
    session.add(_row(created_at=old, updated_at=old))
    session.commit()

    session.execute(text(
        f"UPDATE {_TABLE} SET payout_yen = 1530, html_sha256 = :h "
        "WHERE race_id = :r AND horse_number = 7"
    ), {"r": _RID, "h": "c" * 64})
    session.commit()
    stored = session.execute(text(
        f"SELECT payout_yen, html_sha256, created_at, updated_at FROM {_TABLE} "
        "WHERE race_id = :r"
    ), {"r": _RID}).one()
    assert stored.payout_yen == 1530 and stored.html_sha256 == "c" * 64
    assert stored.created_at == old
    assert stored.updated_at > old

    session.execute(text(f"DELETE FROM {_TABLE} WHERE race_id = :r"), {"r": _RID})
    session.commit()
    assert session.execute(text(f"SELECT count(*) FROM {_TABLE}")).scalar_one() == 0


def test_downgrade_to_0019_removes_the_table_and_upgrade_restores_it(
    alembic_cfg, engine, _migrated
):
    tables_at_head = set(inspect(engine).get_table_names())
    assert _TABLE in tables_at_head
    try:
        command.downgrade(alembic_cfg, "0019_attention_picks")
        assert set(inspect(engine).get_table_names()) == tables_at_head - {_TABLE}
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            # the shared set_updated_at() function belongs to 0001 and must survive
            fn = conn.execute(text(
                "SELECT count(*) FROM pg_proc WHERE proname = 'set_updated_at'"
            )).scalar_one()
        assert version == "0019_attention_picks"
        assert fn == 1

        command.upgrade(alembic_cfg, "head")
        assert set(inspect(engine).get_table_names()) == tables_at_head
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "0020_official_win_payouts"
    finally:
        command.upgrade(alembic_cfg, "head")
