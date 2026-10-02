"""Feature 138 (plan 0.4・T020b, D18): the recorded 300/600-point checkpoint decisions.

Synthetic picks (one race per day, ten picked horses per race, rule S3) settle at the judged odds:
only picks whose post time is at least three days old are material, a 600 decision is written only
after a 300 ``continue`` taken under the current prospective start date, a decision is recorded
once and a late result never changes it, ``--dry-run`` writes nothing, and a failing judgement
never undoes the computation that ran before it.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from decimal import Decimal

import pytest
from horseracing_db.enums import ResultStatus
from horseracing_db.models import (
    AttentionCheckpoint,
    AttentionPick,
    AttentionRaceScan,
    Horse,
    MarketEvPrediction,
    Race,
    RaceResult,
)
from horseracing_eval import attention_rules as ar
from sqlalchemy import select

from horseracing_training import attention_checkpoints
from horseracing_training.cli import main
from tests import _attention_synth as att

pytestmark = pytest.mark.integration

FIRST_DAY = datetime.date(2025, 1, 1)  # in the past: the CLI judges at the real now
HORSES = [f"CH{i}" for i in range(10)]


def _post(i: int) -> datetime.datetime:
    day = FIRST_DAY + datetime.timedelta(days=i)
    return datetime.datetime(day.year, day.month, day.day, 3, tzinfo=datetime.UTC)  # 12:00 JST


def _rid(i: int) -> str:
    day = FIRST_DAY + datetime.timedelta(days=i)
    return f"{day:%Y%m%d}0601"


def seed_picks(session, n_races: int, winner_odds, *, results=True, rule="S3") -> list[str]:
    """n_races races (one per day), ten S3 picks each; horse 0 wins at ``winner_odds(i)``."""
    for h in HORSES:
        session.merge(Horse(horse_id=h, horse_name=h))
    session.flush()
    ids: list[str] = []
    for i in range(n_races):
        rid, post = _rid(i), _post(i)
        session.add(Race(race_id=rid, race_date=post.date(), race_number=1, venue_code="06",
                         distance=1600, track_type="芝", post_time=post))
        session.flush()
        for n, h in enumerate(HORSES):
            pick_id = uuid.uuid4()
            ids.append(str(pick_id))
            session.add(AttentionPick(
                pick_id=pick_id, race_id=rid, horse_id=h, horse_number=n + 1, rule_id=rule,
                rule_set_version=ar.RULE_SET_VERSION, kind="pick",
                ens_expected_return=Decimal("1.5"), single_expected_return=Decimal("1.0"),
                odds_used=Decimal(str(winner_odds(i) if n == 0 else 10.0)),
                odds_observed_at=post - datetime.timedelta(hours=2),
                days_since_last=Decimal("28.0"), post_time=post, seconds_to_post=3600,
                result_pending_at_compute=True, field_digest=ar.field_digest(HORSES),
                ensemble_model_version=att.ENS, single_model_version=att.SINGLE,
                logic_version="test", run_id=uuid.uuid4(),
                selection_policy_version=ar.SELECTION_POLICY_VERSION,
                computed_at=post - datetime.timedelta(hours=1),
            ))
        if results:
            add_results(session, i)
    session.commit()
    return ids


def add_results(session, i: int) -> None:
    for n, h in enumerate(HORSES):
        session.add(RaceResult(race_id=_rid(i), horse_id=h, finish_order=n + 1,
                               result_status=ResultStatus.FINISHED))
    session.flush()


def _alternating(i: int) -> float:
    return 15.0 if i % 2 == 0 else 5.0  # day ROI 1.5 / 0.5: the CI straddles 100%


def _records(session) -> list[AttentionCheckpoint]:
    session.expire_all()
    return list(session.scalars(
        select(AttentionCheckpoint).order_by(AttentionCheckpoint.rule_id,
                                             AttentionCheckpoint.checkpoint)))


@pytest.fixture
def started(monkeypatch):
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", FIRST_DAY)
    return FIRST_DAY


def _evaluate(session, last_race: int, *, days_after: float, **kw) -> dict:
    now = _post(last_race) + datetime.timedelta(days=days_after)
    return attention_checkpoints.evaluate_checkpoints(session, now=now, run_id=uuid.uuid4(),
                                                      **kw)


def test_no_start_date_counts_nothing(session, monkeypatch):
    seed_picks(session, 31, _alternating)
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", None)  # before go-live
    out = _evaluate(session, 30, days_after=10)
    assert out == {"written": [], "pending": [], "error": None, "prospective_start_date": None,
                   "dry_run": False}
    assert _records(session) == []


def test_picks_younger_than_the_settlement_lag_are_not_material(session, started):
    seed_picks(session, 31, _alternating)  # 310 counted picks
    early = _evaluate(session, 30, days_after=1)  # races 29-30 are within three days: 290 material
    assert early["written"] == [] and early["pending"] == [("S3", 300)]
    assert _records(session) == []

    due = _evaluate(session, 30, days_after=3)
    assert due["written"] == [("S3", 300, "continue")] and due["pending"] == []
    (rec,) = _records(session)
    assert (rec.rule_id, rec.checkpoint, rec.decision, rec.n_counted) == ("S3", 300, "continue",
                                                                          300)
    assert rec.settlement_cutoff == _post(30)  # now − 3 days
    assert rec.prospective_start_date == FIRST_DAY
    assert rec.selection_policy_version == ar.SELECTION_POLICY_VERSION
    assert rec.rule_set_version == ar.RULE_SET_VERSION
    assert rec.skipped_pending_before_last == 0
    assert rec.bootstrap["b"] == ar.BOOTSTRAP["b"] and rec.bootstrap["seed"] == ar.BOOTSTRAP["seed"]
    assert rec.ci_low < 1 < rec.ci_high


def test_counted_pick_ids_sha256_is_the_first_300_in_post_order(session, started):
    seed_picks(session, 31, _alternating)
    _evaluate(session, 30, days_after=5)
    (rec,) = _records(session)
    session.expire_all()
    picks = list(session.scalars(select(AttentionPick).where(AttentionPick.kind == "pick")))
    ordered = sorted(picks, key=lambda p: (p.post_time, p.race_id, p.horse_number,
                                           str(p.pick_id)))[:300]
    expected = hashlib.sha256("\n".join(str(p.pick_id) for p in ordered).encode()).hexdigest()
    assert rec.counted_pick_ids_sha256 == expected
    assert rec.last_pick_id == ordered[-1].pick_id
    hits = sum(1 for p in ordered if p.horse_number == 1)
    assert rec.n_hits == hits == 30
    assert rec.roi_frozen == Decimal(repr(sum(float(p.odds_used) for p in ordered
                                              if p.horse_number == 1) * 100 / 30000))


def test_600_is_written_only_after_a_300_continue(session, started):
    seed_picks(session, 62, _alternating)
    out = _evaluate(session, 61, days_after=5)
    assert out["written"] == [("S3", 300, "continue"), ("S3", 600, "undecided")]
    assert [(r.checkpoint, r.decision) for r in _records(session)] == [(300, "continue"),
                                                                       (600, "undecided")]
    # recorded once: a second judgement writes nothing (the records stand)
    again = _evaluate(session, 61, days_after=9)
    assert again["written"] == [] and again["pending"] == [] and again["error"] is None
    assert len(_records(session)) == 2


def test_a_record_written_concurrently_stands_and_nothing_is_added(session, started, monkeypatch):
    """The ON CONFLICT path: the record appears after this judgement read the existing records
    (another writer won the race) — the insert returns nothing, nothing is reported as written,
    and the stored record is unchanged."""
    seed_picks(session, 31, _alternating)
    _evaluate(session, 30, days_after=5)
    (rec,) = _records(session)
    before = (rec.checkpoint_id, rec.decided_at, rec.counted_pick_ids_sha256)
    monkeypatch.setattr(attention_checkpoints, "_existing_records", lambda _session: {})
    out = _evaluate(session, 30, days_after=9)
    assert out["written"] == [] and out["pending"] == [] and out["error"] is None
    (after,) = _records(session)
    assert (after.checkpoint_id, after.decided_at, after.counted_pick_ids_sha256) == before


def test_a_failed_300_is_final(session, started):
    seed_picks(session, 62, lambda i: 2.0)  # every day returns 20%: the CI is below 100%
    out = _evaluate(session, 61, days_after=5)
    assert out["written"] == [("S3", 300, "failed")] and out["pending"] == []
    assert [(r.checkpoint, r.decision) for r in _records(session)] == [(300, "failed")]


def test_a_late_result_never_changes_the_record(session, started):
    ids = seed_picks(session, 32, _alternating, results=False)
    for i in range(32):
        if i != 4:  # race 4's results arrive late
            add_results(session, i)
    session.commit()
    _evaluate(session, 31, days_after=5)
    (rec,) = _records(session)
    # race 4's ten picks precede the material's last pick but had no result yet
    assert rec.skipped_pending_before_last == 10
    before = (rec.checkpoint_id, rec.counted_pick_ids_sha256, rec.decision, rec.last_pick_id)
    assert str(rec.last_pick_id) in ids

    add_results(session, 4)
    session.commit()
    again = _evaluate(session, 31, days_after=9)
    assert again["written"] == []
    (after,) = _records(session)
    assert (after.checkpoint_id, after.counted_pick_ids_sha256, after.decision,
            after.last_pick_id) == before


def test_dry_run_writes_nothing(session, started):
    seed_picks(session, 31, _alternating)
    out = _evaluate(session, 30, days_after=5, dry_run=True)
    assert out["dry_run"] is True and out["written"] == [("S3", 300, "continue")]
    assert _records(session) == []


def test_a_record_under_another_start_date_is_never_extended(session, started, monkeypatch):
    seed_picks(session, 62, _alternating)
    # a 300 continue recorded under the go-live date, with only 300 material at the time
    _evaluate(session, 32, days_after=0)
    assert [(r.checkpoint, r.decision) for r in _records(session)] == [(300, "continue")]
    monkeypatch.setattr(ar, "PROSPECTIVE_START_DATE", FIRST_DAY + datetime.timedelta(days=1))
    out = _evaluate(session, 61, days_after=5)
    assert out["written"] == []
    assert out["error"].startswith(f"{attention_checkpoints.MISMATCH}: S3/300 recorded under")
    assert [r.checkpoint for r in _records(session)] == [300]


def test_voided_picks_are_not_material(session, started):
    ids = seed_picks(session, 31, _alternating)
    first = next(p for p in _picks_of_race(session, 0) if p.horse_number == 2)
    session.add(AttentionPick(
        pick_id=uuid.uuid4(), race_id=first.race_id, horse_id=first.horse_id, rule_id="S3",
        rule_set_version=ar.RULE_SET_VERSION, kind="void", void_reason="scratched",
        voids_pick_id=first.pick_id, result_pending_at_compute=True,
        field_digest=first.field_digest, ensemble_model_version=att.ENS,
        single_model_version=att.SINGLE, logic_version="test", run_id=uuid.uuid4(),
        selection_policy_version=ar.SELECTION_POLICY_VERSION,
    ))
    session.commit()
    assert str(first.pick_id) in ids
    _evaluate(session, 30, days_after=5)
    (rec,) = _records(session)
    # with one pick of race 0 voided, the 300th pick moves one place later (into race 30)
    assert rec.last_pick_id in {p.pick_id for p in _picks_of_race(session, 30)}


def _picks_of_race(session, i: int) -> list[AttentionPick]:
    session.expire_all()
    return list(session.scalars(select(AttentionPick).where(
        AttentionPick.race_id == _rid(i), AttentionPick.kind == "pick")))


def test_a_failing_judgement_never_undoes_the_computation(session, database_url, tmp_path,
                                                           monkeypatch, capsys):
    att.seed_card(session)
    single, ensemble = att.model_dirs(tmp_path)
    forced = att.ForcedEv(monkeypatch)
    forced.ens[(att.A, "H4")] = 1.5

    def _boom(*_a, **_k):
        raise RuntimeError("checkpoint\nstore unavailable")

    monkeypatch.setattr(attention_checkpoints, "evaluate_checkpoints", _boom)
    out = att.run(session, single, ensemble)
    assert out["status"] == "ok"
    assert out["checkpoints"] == {"written": [], "pending": [],
                                  "error": "RuntimeError: checkpoint store unavailable"}
    session.expire_all()
    assert session.scalars(select(MarketEvPrediction)).first() is not None
    assert len(list(session.scalars(select(AttentionRaceScan)))) == 3
    assert len(list(session.scalars(select(AttentionPick)))) == 4

    base = ["--model-dir", str(single), "--ensemble-dir", str(ensemble),
            "--database-url", database_url]
    assert main(["market-ev", "--date", att.DAY.isoformat(), *base]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1].endswith("versions=2 picks=0 checkpoints=error")
    assert lines[-2] == "attention-checkpoints: error=RuntimeError: checkpoint store unavailable"


def test_cli_attention_checkpoints_end_to_end(session, database_url, started, capsys):
    seed_picks(session, 31, _alternating)  # the races ran in 2025-01: all past the lag now
    assert main(["attention-checkpoints", "--dry-run", "--database-url", database_url]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == (
        "OK: written=1 pending=0 dry_run=true"
    )
    assert _records(session) == []
    assert main(["attention-checkpoints", "--database-url", database_url]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "OK: written=1 pending=0 dry_run=false"
    assert out[-2].startswith("attention-checkpoints: written=S3:300:continue pending=- ")
    assert [(r.checkpoint, r.decision) for r in _records(session)] == [(300, "continue")]
