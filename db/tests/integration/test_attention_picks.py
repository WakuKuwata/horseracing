"""Feature 138 (T012): attention_* tables — append-only at the database boundary, one pick per
(race, horse, rule, rule-set version) for life, one void per pick, one decision per checkpoint.

Runs under the runtime connection role (locally the table OWNER), so the protection against
UPDATE / DELETE / TRUNCATE must come from the triggers (0017 precedent).
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from horseracing_db.models import AttentionCheckpoint, AttentionPick, AttentionRaceScan, Race

pytestmark = pytest.mark.integration

_RID = "202611010511"
_TABLES = ("attention_race_scans", "attention_picks", "attention_checkpoints")
_NOW = datetime.datetime(2026, 11, 1, 1, 0, tzinfo=datetime.UTC)
_RSV = "attention-S1-S5-v1"


def _seed_race(session: Session) -> None:
    session.add(Race(race_id=_RID, race_number=11, race_date=datetime.date(2026, 11, 1)))
    session.flush()


def _pick(**kw) -> AttentionPick:
    values = dict(
        pick_id=uuid.uuid4(), race_id=_RID, horse_id="h1", horse_number=5, rule_id="S1",
        rule_set_version=_RSV, kind="pick", ens_expected_return=Decimal("1.31"),
        single_expected_return=Decimal("1.28"), odds_used=Decimal("32.5"),
        odds_observed_at=_NOW - datetime.timedelta(minutes=3), days_since_last=Decimal("42"),
        post_time=_NOW + datetime.timedelta(hours=5), seconds_to_post=18000,
        result_pending_at_compute=True, field_digest="d" * 64,
        ensemble_model_version="mev-ens15-v1", single_model_version="mev-binary-v2",
        logic_version="lv;policy=v1", run_id=uuid.uuid4(), selection_policy_version="v1",
        computed_at=_NOW,
    )
    values.update(kw)
    return AttentionPick(**values)


def _void(target: AttentionPick, **kw) -> AttentionPick:
    values = dict(
        pick_id=uuid.uuid4(), race_id=_RID, horse_id=target.horse_id, horse_number=None,
        rule_id=target.rule_id, rule_set_version=target.rule_set_version, kind="void",
        void_reason="scratched", voids_pick_id=target.pick_id, ens_expected_return=None,
        single_expected_return=None, odds_used=None, odds_observed_at=None, days_since_last=None,
        result_pending_at_compute=True, field_digest="e" * 64,
        ensemble_model_version="mev-ens15-v1", single_model_version="mev-binary-v2",
        logic_version="lv;policy=v1", run_id=uuid.uuid4(), selection_policy_version="v1",
        computed_at=_NOW + datetime.timedelta(hours=1),
    )
    values.update(kw)
    return AttentionPick(**values)


def _scan(**kw) -> AttentionRaceScan:
    values = dict(race_id=_RID, rule_set_version=_RSV, run_id=uuid.uuid4(), computed_at=_NOW,
                  post_time=_NOW + datetime.timedelta(hours=5), result_pending_at_compute=True,
                  field_digest="d" * 64, n_picks=0)
    values.update(kw)
    return AttentionRaceScan(**values)


def _checkpoint(last: AttentionPick, **kw) -> AttentionCheckpoint:
    values = dict(
        checkpoint_id=uuid.uuid4(), rule_id="S1", checkpoint=300, selection_policy_version="v1",
        rule_set_version=_RSV, decision="continue", n_counted=300, n_hits=12,
        roi_frozen=Decimal("1.05"), ci_low=Decimal("0.8"), ci_high=Decimal("1.3"),
        bootstrap={"b": 20000, "seed": 20260905}, counted_pick_ids_sha256="f" * 64,
        last_pick_id=last.pick_id, settlement_cutoff=_NOW,
        prospective_start_date=datetime.date(2026, 10, 15), skipped_pending_before_last=0,
    )
    values.update(kw)
    return AttentionCheckpoint(**values)


def _commit_expecting(session: Session, exc, match: str | None = None, *objs) -> None:
    session.add_all(objs)
    with pytest.raises(exc, match=match):
        session.commit()
    session.rollback()


# ------------------------------------------------------------------ uniqueness and constraints


def test_one_pick_per_race_horse_rule_and_rule_set_version(session: Session):
    _seed_race(session)
    session.add(_pick())
    session.commit()
    _commit_expecting(session, IntegrityError, None, _pick())
    session.add(_pick(rule_id="S3"))  # another rule is fine
    session.add(_pick(rule_set_version="attention-S1-S5-v2"))  # another rule-set version is fine
    session.commit()
    assert session.execute(text("SELECT count(*) FROM attention_picks")).scalar_one() == 3


def test_one_void_per_pick_and_void_shape(session: Session):
    _seed_race(session)
    target = _pick()
    session.add(target)
    session.commit()
    session.add(_void(target))
    session.commit()
    _commit_expecting(session, IntegrityError, None, _void(target))
    _commit_expecting(session, IntegrityError, None, _void(target, void_reason="field_changed",
                                                            voids_pick_id=_pick().pick_id))
    _commit_expecting(session, IntegrityError, None, _void(target, void_reason=None))
    _commit_expecting(session, IntegrityError, None, _void(target, voids_pick_id=None))


@pytest.mark.parametrize(
    "kw",
    [
        dict(kind="other"),
        dict(rule_id="S6"),
        dict(void_reason="scratched"),  # a pick may not carry a void reason
        dict(horse_number=None),
        dict(odds_used=Decimal("0.9")),
        dict(ens_expected_return=None),
        dict(odds_observed_at=None),
    ],
)
def test_pick_checks(session: Session, kw):
    _seed_race(session)
    session.commit()
    _commit_expecting(session, IntegrityError, None, _pick(**kw))


def test_scan_primary_key_and_n_picks(session: Session):
    _seed_race(session)
    session.add(_scan())
    session.commit()
    _commit_expecting(session, IntegrityError, None, _scan())
    _commit_expecting(session, IntegrityError, None, _scan(rule_set_version="x", n_picks=-1))
    session.add(_scan(rule_set_version="attention-S1-S5-v2", n_picks=3))
    session.commit()


def test_scan_insert_on_conflict_returns_nothing_the_second_time(session: Session):
    """The training job decides 'first computation' with exactly this statement (plan D16)."""
    _seed_race(session)
    session.commit()
    stmt = text(
        "INSERT INTO attention_race_scans (race_id, rule_set_version, run_id, computed_at, "
        "post_time, result_pending_at_compute, field_digest, n_picks) "
        "VALUES (:r, :v, :run, now(), NULL, true, 'd', 0) "
        "ON CONFLICT (race_id, rule_set_version) DO NOTHING RETURNING race_id"
    )
    first = session.execute(stmt, {"r": _RID, "v": _RSV, "run": uuid.uuid4()}).all()
    second = session.execute(stmt, {"r": _RID, "v": _RSV, "run": uuid.uuid4()}).all()
    session.commit()
    assert first == [(_RID,)] and second == []


def test_checkpoint_uniqueness_and_checks(session: Session):
    _seed_race(session)
    last = _pick()
    session.add(last)
    session.commit()
    session.add(_checkpoint(last))
    session.commit()
    _commit_expecting(session, IntegrityError, None, _checkpoint(last, decision="passed"))
    _commit_expecting(session, IntegrityError, None,
                      _checkpoint(last, rule_id="S2", decision="undecided"))
    _commit_expecting(session, IntegrityError, None,
                      _checkpoint(last, rule_id="S2", checkpoint=600, n_counted=600,
                                  decision="continue"))
    _commit_expecting(session, IntegrityError, None, _checkpoint(last, rule_id="S2", n_counted=299))
    session.add(_checkpoint(last, checkpoint=600, n_counted=600, decision="undecided"))
    session.add(_checkpoint(last, selection_policy_version="v2"))
    session.commit()


# ------------------------------------------------------------------ append-only


def _seed_all(session: Session) -> None:
    _seed_race(session)
    p = _pick()
    session.add_all([_scan(), p])
    session.flush()
    session.add(_checkpoint(p))
    session.commit()


@pytest.mark.parametrize("table", _TABLES)
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE {t} SET rule_set_version = 'x'",
        "DELETE FROM {t}",
        "TRUNCATE TABLE {t} CASCADE",
    ],
)
def test_mutations_are_rejected(session: Session, table, sql):
    _seed_all(session)
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text(sql.format(t=table)))
        session.commit()
    session.rollback()


# ------------------------------------------------------------------ schema contract


def test_partial_unique_indexes(engine):
    idx = {i["name"]: i for i in inspect(engine).get_indexes("attention_picks")}
    pick = idx["uq_attention_picks_pick"]
    assert pick["unique"] and pick["column_names"] == [
        "race_id", "horse_id", "rule_id", "rule_set_version"]
    assert "kind = 'pick'" in pick["dialect_options"]["postgresql_where"]
    void = idx["uq_attention_picks_void"]
    assert void["unique"] and void["column_names"] == ["voids_pick_id"]
    assert "kind = 'void'" in void["dialect_options"]["postgresql_where"]
    assert {"ix_attention_picks_race", "ix_attention_picks_rule_computed"} <= set(idx)


def test_triggers_exist(engine):
    with engine.connect() as conn:
        names = set(conn.execute(text(
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'trg_attention_%'"
        )).scalars())
    assert names == {f"trg_{t}_reject_{k}" for t in _TABLES for k in ("mutation", "truncate")}


def test_downgrade_to_0018_removes_tables_and_function(alembic_cfg, engine, _migrated):
    tables_at_head = set(inspect(engine).get_table_names())
    assert set(_TABLES) <= tables_at_head
    try:
        command.downgrade(alembic_cfg, "0018_market_ev_predictions")
        # Feature 139's official_win_payouts (0020) goes down with them
        assert set(inspect(engine).get_table_names()) == (
            tables_at_head - set(_TABLES) - {"official_win_payouts"}
        )
        with engine.connect() as conn:
            fn = conn.execute(text(
                "SELECT count(*) FROM pg_proc WHERE proname = 'reject_attention_mutation'"
            )).scalar_one()
        assert fn == 0
        command.upgrade(alembic_cfg, "head")
        assert set(inspect(engine).get_table_names()) == tables_at_head
    finally:
        command.upgrade(alembic_cfg, "head")
